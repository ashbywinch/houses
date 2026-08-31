# lucidlint: ignore bulk-suppression per-site whys are mandated (review-log scope decision 5: no config ignores)
"""Broadcaster — pushes fresh property summaries to WebSocket clients.

When a DAG node finishes recomputing (via _processor), _push_node_update
sends its value to all WebSocket clients. The _broadcaster coroutine pops
RID-level events from _broadcast_queue and pushes full-property summaries.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket

from houses.services_provider import get_services

logger = logging.getLogger(__name__)

_broadcast_queue: asyncio.Queue[str] = asyncio.Queue()
_websocket_clients: set[WebSocket] = set()


def _reset():
    """Reset broadcast queue and websocket clients for test isolation."""
    # lucidlint: ignore global-state deliberate test seam — _reset() swaps the queue for test isolation
    global _broadcast_queue
    _broadcast_queue = asyncio.Queue()
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


async def _push_node_update(node) -> None:
    """Push a node's latest value to all WebSocket clients."""

    rid = node._id.split("/")[0]
    try:
        data = await node.to_json()
    # lucidlint: ignore broad-except serialisation failure silently drops this push; clients refresh on next change
    except Exception:
        return
    # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    msg = json.dumps({"type": "node_updated", "rid": rid, "node_id": node._id, "data": data})
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


async def _broadcaster() -> None:
    """Pop completed RIDs from the queue and push full-property summaries."""

    while True:
        rid = await _broadcast_queue.get()
        if not _websocket_clients:
            continue
        prop = get_services().property_registry.get(rid)
        if prop is None:
            continue
        try:
            summary = await prop.to_json_summary()
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
        # lucidlint: ignore broad-except loop boundary — one property's broadcast failure must not kill the broadcaster
        except Exception as exc:
            logger.warning("Broadcast failed for %s: %s", rid, exc)
            continue

