"""REST API for the reactive DAG.

Endpoints return node ``to_json()`` output — the API never accesses
node internals directly.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from houses.nodes.property import PropertyNodes
from houses.nodes.settings import settings_node

api_router = APIRouter(prefix="/api")

_registry: dict[str, PropertyNodes] = {}


@api_router.get("/properties")
async def list_properties():
    """List all registered property RIDs."""
    return {"properties": list(_registry.keys())}


@api_router.get("/properties/{rid}")
async def get_property(rid: str):
    """Return a single property's DAG node values."""
    prop = _registry.get(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    return prop.to_json()


@api_router.get("/settings")
async def get_settings():
    """Return the current settings."""
    return settings_node.attempt().value_or_none() or {}
