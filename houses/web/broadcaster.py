# lucidlint: ignore bulk-suppression per-site whys are mandated (review-log scope decision 5: no config ignores)
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
from dataclasses import dataclass

from fastapi import WebSocket

from houses.nodes.property_nodes import SummaryJson
from houses.services_provider import get_services
from houses.web.monthly_delta import CURRENT_STATUS
from houses.web.monthly_delta import attach as attach_monthly_delta
from houses.web.settings_payload import SettingsPayloadJson, settings_payload

logger = logging.getLogger(__name__)

_broadcast_queue: asyncio.Queue[str] = asyncio.Queue()
_websocket_clients: set[WebSocket] = set()


def _reset():
    """Reset broadcast state for test isolation."""
    global _broadcast_queue, _pending_notify_rids, _notify_debounce_task
    _broadcast_queue = asyncio.Queue()
    _pending_notify_rids = set()
    _notify_debounce_task = None
    _websocket_clients.clear()


async def register_client(ws: WebSocket) -> None:
    """Register a WebSocket client and keep the connection alive."""
    await ws.accept()
    _websocket_clients.add(ws)
    try:
        while True:
            try:
                await ws.receive_text()
            # lucidlint: ignore broad-except connection boundary — any receive failure means the client is gone; drop it
            except Exception:
                break
    finally:
        _websocket_clients.discard(ws)


@dataclass(frozen=True)
class _PropertyUpdatedEnvelope:
    """The property_updated websocket message envelope (wire shape)."""

    rid: str
    data: dict

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return dict(type="property_updated", rid=self.rid, data=self.data)



@dataclass(frozen=True)
class _SettingsUpdatedEnvelope:
    """The settings_updated websocket message envelope (wire shape)."""

    data: SettingsPayloadJson

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return dict(type="settings_updated", data=self.data.to_dict())

async def _push_summary(rid: str) -> SummaryJson | None:
    """Build, delta-attach, and push one property's summary to all clients.

    Returns the pushed summary record, or None when the rid has no
    registry property (it vanished between enqueue and dequeue)."""
    prop = get_services().property_registry.get(rid)
    if prop is None:
        return None
    # property_nodes.to_json_summary still returns the wire dict (its
    # record conversion is out of this wave's file set) — reconstruct the
    # record at the consumption boundary, then serialize at the edge.
    summary = SummaryJson(**await prop.to_json_summary())
    wire = summary.to_dict()
    await attach_monthly_delta(wire, rid, get_services().property_registry)
    msg = json.dumps(_PropertyUpdatedEnvelope(rid=rid, data=wire).to_dict())
    dead: list[WebSocket] = []
    for ws in list(_websocket_clients):
        try:
            await ws.send_text(msg)
        # lucidlint: ignore broad-except connection boundary — any send failure discards the dead client
        except Exception as e:
            logger.debug("client websocket send failed (discarding client): %s", e)
            dead.append(ws)
            continue
    for ws in dead:
        _websocket_clients.discard(ws)
    return summary


def _is_current_home(rid: str) -> bool:
    """The pushed property IS the current home — then every other card's
    delta_vs_home just went stale (the broadcaster sweeps them). Mirrors
    monthly_delta._status_is_current so the sweep agrees with the attach."""
    prop = get_services().property_registry.get(rid)
    if prop is None:
        return False
    att = prop.comment_status.latest_attempt()
    return att.succeeded and (att.value_or_none() or "").strip().lower() == CURRENT_STATUS


async def _broadcaster() -> None:
    """Pop completed RIDs from the queue and push full-property summaries.

    Freshness: when the pushed property IS the current home, every other
    card's delta_vs_home just went stale — fresh summaries for the rest of
    the registry are built and pushed DIRECTLY here (never re-enqueued
    through the queue: that would loop)."""
    while True:
        rid = await _broadcast_queue.get()
        if not _websocket_clients:
            continue
        try:
            summary = await _push_summary(rid)
            if summary is not None and _is_current_home(rid):
                for other_rid in get_services().property_registry.list_properties():
                    if other_rid == rid:
                        continue
                    try:
                        await _push_summary(other_rid)
                    # lucidlint: ignore broad-except loop boundary — one stale summary must not kill the sweep
                    except Exception as exc:
                        logger.warning("Broadcast failed for %s: %s", other_rid, exc)
                        continue
        # lucidlint: ignore broad-except loop boundary — one property's broadcast failure must not kill the broadcaster
        except Exception as exc:
            logger.warning("Broadcast failed for %s: %s", rid, exc)
            continue


_pending_notify_rids: set[str] = set()
_notify_debounce_task: asyncio.Task | None = None
_NOTIFY_DEBOUNCE_SECONDS = 0.4


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
    global _notify_debounce_task
    rid = getattr(node, "property_rid", None)
    if rid is None:
        return  # not a property view — nothing to coalesce against
    _pending_notify_rids.add(rid)
    if _notify_debounce_task is None or _notify_debounce_task.done():
        _notify_debounce_task = asyncio.create_task(_flush_notifies())


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
    msg = json.dumps(_SettingsUpdatedEnvelope(data=payload).to_dict())
    dead: list[WebSocket] = []
    for ws in list(_websocket_clients):
        try:
            await ws.send_text(msg)
        # lucidlint: ignore broad-except connection boundary — any send failure discards the dead client
        except Exception as e:
            logger.debug("client websocket send failed (discarding client): %s", e)
            dead.append(ws)
            continue
    for ws in dead:
        _websocket_clients.discard(ws)


async def _flush_notifies() -> None:
    await asyncio.sleep(_NOTIFY_DEBOUNCE_SECONDS)
    rids = list(_pending_notify_rids)
    _pending_notify_rids.clear()
    for rid in rids:
        await _broadcast_queue.put(rid)
