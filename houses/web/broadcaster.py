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

from fastapi import WebSocket

from houses.services_provider import get_services
from houses.web.monthly_delta import attach as attach_monthly_delta
from houses.web.settings_payload import settings_payload

logger = logging.getLogger(__name__)

_broadcast_queue: asyncio.Queue[str] = asyncio.Queue()
_websocket_clients: set[WebSocket] = set()


def _reset():
    """Reset broadcast state for test isolation."""
    # lucidlint: ignore global-state deliberate test seam — _reset() swaps the queue for test isolation
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


async def _push_summary(rid: str) -> dict | None:
    """Build, delta-attach, and push one property's summary to all clients.

    Returns the pushed summary, or None when the rid has no registry
    property (it vanished between enqueue and dequeue)."""
    prop = get_services().property_registry.get(rid)
    if prop is None:
        return None
    summary = await prop.to_json_summary()
    await attach_monthly_delta(summary, rid, get_services().property_registry)
    # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape
    msg = json.dumps({"type": "property_updated", "rid": rid, "data": summary})
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
            if summary is not None and summary.get("is_current_home"):
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
    """
    payload = await settings_payload()
    msg = json.dumps({"type": "settings_updated", "data": payload})
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
