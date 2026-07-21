"""Broadcaster — pushes fresh property summaries to WebSocket clients.

When a DAG node finishes recomputing (via _processor), its RID is pushed
to _broadcast_queue.  The _broadcaster picks it up, recomputes the full
summary for that property, and sends it to all connected WebSocket clients.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_broadcast_queue: asyncio.Queue[str] = asyncio.Queue()
_websocket_clients: set[WebSocket] = set()


def _reset():
    """Reset broadcast queue and websocket clients for test isolation."""
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
            except Exception:
                break
    finally:
        _websocket_clients.discard(ws)


async def _push_node_update(node) -> None:
    """Push a node's latest value to all WebSocket clients."""

    rid = node._id.split("/")[0]
    try:
        data = await node.to_json()
    except Exception:
        return
    msg = json.dumps({"type": "node_updated", "rid": rid, "node_id": node._id, "data": data})
    dead: list[WebSocket] = []
    for ws in list(_websocket_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _websocket_clients.discard(ws)


def push_rid(rid: str) -> None:
    """Push a property RID to the broadcast queue (add/delete events only)."""
    _broadcast_queue.put_nowait(rid)


async def _broadcaster() -> None:
    """Pop completed RIDs from the queue and push full-property summaries."""
    from houses.property_registry import get_property

    while True:
        rid = await _broadcast_queue.get()
        if not _websocket_clients:
            continue
        prop = get_property(rid)
        if prop is None:
            continue
        try:
            summary = await prop.to_json_summary()
            msg = json.dumps({"type": "property_updated", "rid": rid, "data": summary})
            dead: list[WebSocket] = []
            for ws in list(_websocket_clients):
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _websocket_clients.discard(ws)
        except Exception as exc:
            logger.warning("Broadcast failed for %s: %s", rid, exc)
