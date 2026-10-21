"""Broadcaster — pushes fresh property summaries to WebSocket clients.

When DAG state changes (via _processor), _on_node_refreshed routes
the event: property nodes queue a summary broadcast (one per property,
coalesced), settings nodes push one settings payload. The _broadcaster
coroutine pops RID-level events from _broadcast_queue and pushes
full-property summaries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import partial
from typing import Any, Literal

from fastapi import WebSocket

from dag.guard import capture_async
from houses.nodes.current_home_node import CurrentHomeNode
from houses.nodes.property_summary import PropertySummary
from houses.services_provider import get_services
from houses.web.monthly_delta import attach as attach_monthly_delta
from houses.web.settings_payload import SettingsPayload, settings_payload
from serialization import SerializableAsDict

logger = logging.getLogger(__name__)

class _BroadcastState:
    """The broadcaster's process state — the RID event queue, connected
    clients, the notify-coalescing set + debounce task, and the stale-rid
    set — one owner, reset whole for test isolation."""

    queue: asyncio.Queue[str] = asyncio.Queue()
    websocket_clients: set[WebSocket] = set()
    pending_notify_rids: set[str] = set()
    notify_debounce_task: asyncio.Task | None = None
    # Properties whose broadcast push failed — their cards are silently
    # stale until a later push succeeds. Surfaced on the listing wire so the
    # frontend can mark the card 'not being updated'.
    stale_push_rids: set[str] = set()
    NOTIFY_DEBOUNCE_SECONDS = 0.4


def _mark_broadcast_failed(rid: str, exc: object) -> None:
    """One property's failed broadcast must not kill the broadcaster:
    record the staleness (the frontend marks the card) and let the loop
    continue."""
    logger.warning("Broadcast failed for %s: %s", rid, exc)
    _BroadcastState.stale_push_rids.add(rid)



def stale_push_rids() -> frozenset[str]:
    """Rids whose last broadcast push failed (frontend staleness marker)."""
    return frozenset(_BroadcastState.stale_push_rids)


def _reset():
    """Reset broadcast state for test isolation."""
    _BroadcastState.queue = asyncio.Queue()
    _BroadcastState.pending_notify_rids = set()
    _BroadcastState.notify_debounce_task = None
    _BroadcastState.stale_push_rids = set()
    _BroadcastState.websocket_clients.clear()


async def register_client(ws: WebSocket) -> None:
    """Register a WebSocket client and keep the connection alive."""
    await ws.accept()
    _BroadcastState.websocket_clients.add(ws)
    try:
        while True:
            _, failure = await capture_async(ws.receive_text)
            if failure is not None:
                break
    finally:
        _BroadcastState.websocket_clients.discard(ws)


class _PropertyUpdatedEnvelope(SerializableAsDict):
    """The property_updated websocket message: one property's fresh summary.

    The class owns the message bytes — ``to_json()`` is the only place the
    envelope becomes text, so the key order and separators live here.
    """

    type: Literal["property_updated"] = "property_updated"
    rid: str
    data: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.model_dump())


class _SettingsUpdatedEnvelope(SerializableAsDict):
    """The settings_updated websocket message: the whole settings document."""

    type: Literal["settings_updated"] = "settings_updated"
    data: SettingsPayload

    def to_json(self) -> str:
        return json.dumps(self.model_dump(by_alias=True))


async def _push_summary(rid: str) -> PropertySummary | None:
    """Build, delta-attach, and push one property's summary to all clients.

    Returns the pushed summary record, or None when the rid has no
    registry property (it vanished between enqueue and dequeue)."""
    prop = get_services().property_registry.get(rid)
    if prop is None:
        return None
    summary = await prop.summary()
    wire = dict(summary.to_dict())
    await attach_monthly_delta(wire, rid)
    msg = _PropertyUpdatedEnvelope(rid=rid, data=wire).to_json()
    await _broadcast_wire(msg)
    return summary


async def _broadcast_wire(msg: str) -> None:
    """Send one wire message to every client; drop the ones that fail."""
    dead: list[WebSocket] = []
    for ws in list(_BroadcastState.websocket_clients):
        _, failure = await capture_async(partial(ws.send_text, msg))
        if failure is not None:
            logger.debug("client websocket send failed (discarding client): %s", failure)
            dead.append(ws)
    for ws in dead:
        _BroadcastState.websocket_clients.discard(ws)


def _is_current_home(rid: str) -> bool:
    """The pushed property IS the current home — then every other card's
    delta_vs_home just went stale (the broadcaster sweeps them). Mirrors
    monthly_delta._status_is_current so the sweep agrees with the attach."""
    prop = get_services().property_registry.get(rid)
    if prop is None:
        return False
    att = prop.comment_status.latest_attempt()
    return att.succeeded and (att.value_or_none() or "").strip().lower() == CurrentHomeNode.CURRENT_STATUS


async def _broadcaster() -> None:
    """Pop completed RIDs from the queue and push full-property summaries.

    Freshness: when the pushed property IS the current home, every other
    card's delta_vs_home just went stale — fresh summaries for the rest of
    the registry are built and pushed DIRECTLY here (never re-enqueued
    through the queue: that would loop)."""
    while True:
        rid = await _BroadcastState.queue.get()
        if not _BroadcastState.websocket_clients:
            continue
        if await _push_or_mark(rid) and _is_current_home(rid):
            for other_rid in get_services().property_registry.list_properties():
                if other_rid == rid:
                    continue
                await _push_or_mark(other_rid)


async def _push_or_mark(rid: str) -> bool:
    """Push one property's summary; mark the rid stale when it fails.

    Returns True when the push landed — the caller sweeps the other
    cards only after a landed push (a failed one is already marked).
    """
    summary, failure = await capture_async(partial(_push_summary, rid))
    if failure is not None:
        _mark_broadcast_failed(rid, failure)
        return False
    if summary is not None:
        _BroadcastState.stale_push_rids.discard(rid)
    return True



async def notify_node_refreshed_async(node) -> None:
    """THE DAG→frontend seam: a property node refreshed, so the property's
    summary is queued for broadcast (coalesced — a cascade touching many
    nodes of one property pushes that property once).

    Any recompute path lands here automatically: settings edits, what-if
    applies, scrape applies. Callers never remember to notify — the DAG
    refresh is the notification.

    Runs on the MAIN loop (the broadcaster's loop): the processor hands
    it over via run_coroutine_threadsafe, keeping every asyncio object
    here owned by one loop.
    """
    rid = getattr(node, "property_rid", None)
    if rid is None:
        return  # not a property view — nothing to coalesce against
    _BroadcastState.pending_notify_rids.add(rid)
    if _BroadcastState.notify_debounce_task is None or _BroadcastState.notify_debounce_task.done():
        _BroadcastState.notify_debounce_task = asyncio.create_task(_flush_notifies())


async def push_settings_updated() -> None:
    """THE DAG→frontend seam for settings: a settings node refreshed, so
    the settings payload (persons, thresholds, what-if flag) is pushed
    to connected clients. Coalesced per debounce window upstream. Runs
    on the MAIN loop — the processor hands it over via
    run_coroutine_threadsafe, keeping every asyncio object here owned
    by one loop.

    The payload is SESSION-NEUTRAL by construction (settings_payload()
    with no session_user): its per-session fields — ``editable_by_me`` —
    are false for everyone, because one broadcast cannot carry per-client
    ownership. Consumers that need ownership read it from the
    session-scoped endpoint (GET /api/settings); the push is for
    thresholds, ceilings, labels, and the what-if flag.
    """
    payload = await settings_payload()
    msg = _SettingsUpdatedEnvelope(data=payload).to_json()
    await _broadcast_wire(msg)


async def _flush_notifies() -> None:
    await asyncio.sleep(_BroadcastState.NOTIFY_DEBOUNCE_SECONDS)
    rids = list(_BroadcastState.pending_notify_rids)
    _BroadcastState.pending_notify_rids.clear()
    for rid in rids:
        await _BroadcastState.queue.put(rid)
