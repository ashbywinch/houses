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


def push_rid(rid: str) -> None:
    """Push a property RID to the broadcast queue."""
    _broadcast_queue.put_nowait(rid)


async def _broadcaster() -> None:
    """Pop completed RIDs from the queue and push summaries."""
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
            for ws in _websocket_clients:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _websocket_clients.discard(ws)
        except Exception as exc:
            logger.warning("Broadcast failed for %s: %s", rid, exc)
