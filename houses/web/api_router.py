"""REST + WebSocket API for the reactive DAG.

Endpoints return node ``to_json()`` output — the API never accesses
node internals directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from houses.nodes.property import PropertyNodes
from houses.nodes.settings import settings_node

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/api")

_registry: dict[str, PropertyNodes] = {}

_websocket_clients: set[WebSocket] = set()


async def _broadcast(data: dict[str, Any]) -> None:
    """Send a JSON message to all connected WebSocket clients."""
    message = json.dumps(data)
    dead: list[WebSocket] = []
    for ws in _websocket_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _websocket_clients.discard(ws)


def register_property(rid: str, prop: PropertyNodes) -> None:
    """Register a property and wire its changed signal to WebSocket broadcast."""
    _registry[rid] = prop
    prop.changed.connect(lambda: asyncio.ensure_future(
        _broadcast({"type": "property_updated", "rid": rid, "data": prop.to_json()})
    ))


@api_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _websocket_clients.add(websocket)
    try:
        # Send initial state
        await websocket.send_json({
            "type": "init",
            "properties": list(_registry.keys()),
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _websocket_clients.discard(websocket)


@api_router.get("/properties")
async def list_properties():
    """List all registered property RIDs."""
    return {"properties": list(_registry.keys())}


@api_router.get("/properties/all")
async def get_all_properties():
    """Return all registered properties' DAG node values."""
    return {
        rid: prop.to_json()
        for rid, prop in _registry.items()
    }


@api_router.get("/properties/{rid}")
async def get_property(rid: str):
    """Return a single property's DAG node values."""
    prop = _registry.get(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    return prop.to_json()


@api_router.post("/seed")
async def seed_properties():
    """Bootstrap the registry from all existing sheet properties."""
    from houses.nodes.bootstrap import seed_registry_from_sheet

    count = seed_registry_from_sheet()
    return {"seeded": count, "total": len(_registry)}


@api_router.get("/settings")
async def get_settings():
    """Return the current settings."""
    return settings_node.attempt().value_or_none() or {}
