from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect

from houses.context import get_services
from houses.nodes.property import PropertyNodes

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/api")

_registry: dict[str, PropertyNodes] = {}

_websocket_clients: set[WebSocket] = set()


async def _broadcast(data: dict[str, Any]) -> None:
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
    async def _on_changed() -> None:
        await _broadcast({
            "type": "property_updated",
            "rid": rid,
            "data": await prop.to_json(),
        })

    _registry[rid] = prop
    prop.changed.connect(lambda: asyncio.ensure_future(_on_changed()))


@api_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _websocket_clients.add(websocket)
    try:
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
    return {"properties": list(_registry.keys())}


def _score_from_summary(s: dict) -> int:
    """Compute card score matching old ``card_data`` formula:
    green=2, orange=1, red=-1, muted=0, summed across 8 metrics.
    """
    def _commute_score(minutes: int | None, bracknell: bool = False) -> int:
        if minutes is None:
            return 0
        if bracknell:
            return 2 if minutes < 30 else (1 if minutes <= 60 else -1)
        return 2 if minutes < 45 else (1 if minutes <= 75 else -1)

    def _ofsted_score(rating: str | None) -> int:
        if rating == "Outstanding":
            return 2
        if rating == "Good":
            return 1
        if rating in ("Requires Improvement", "Inadequate"):
            return -1
        return 0

    def _walk_score(minutes: int | None) -> int:
        if minutes is None:
            return 0
        return 2 if minutes < 15 else (1 if minutes <= 30 else -1)

    score = 0
    for key, cd in s.get("commutes", {}).items():
        c = cd.get("commute", {})
        dur = c.get("value", {}).get("duration", {}).get("value") if c.get("status") == "succeeded" else None
        if dur is not None:
            score += _commute_score(dur, bracknell="Bracknell" in key)
    ps = s.get("schools", {}).get("primary", {}).get("school", {}).get("value", {})
    if ps:
        score += _ofsted_score(ps.get("ofsted"))
        walk = ps.get("walk_minutes")
        if walk is not None:
            score += _walk_score(walk)
    ss = s.get("schools", {}).get("secondary", {}).get("school", {}).get("value", {})
    if ss:
        score += _ofsted_score(ss.get("ofsted"))
        walk = ss.get("walk_minutes")
        if walk is not None:
            score += _walk_score(walk)
    walk_val = s.get("walkability", {})
    if isinstance(walk_val, dict):
        wv = walk_val.get("value")
        if isinstance(wv, dict):
            wt = wv.get("walk_to_town_minutes")
            if wt is not None:
                score += _walk_score(int(wt))
    return score


@api_router.get("/properties/all")
async def get_all_properties():
    results: dict[str, dict] = {}
    for rid, prop in _registry.items():
        results[rid] = await prop.to_json_summary()
    scored = sorted(results.items(), key=lambda kv: _score_from_summary(kv[1]), reverse=True)
    return dict(scored)


@api_router.get("/properties/{rid}")
async def get_property(rid: str):
    prop = _registry.get(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    return await prop.to_json()


@api_router.get("/properties/{rid}/detail")
async def get_property_detail(rid: str):
    prop = _registry.get(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    return await prop.to_json_detail()


@api_router.patch("/properties/{rid}/address")
async def patch_address(rid: str, body: dict):
    prop = _registry.get(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")

    prop.corrected_address.push(body.get("address", ""), "user")
    return {"status": "ok"}


@api_router.patch("/properties/{rid}/location")
async def patch_location(rid: str, body: dict):
    prop = _registry.get(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    from houses.geo import GeoPoint

    gp = GeoPoint(lat=body["lat"], lon=body["lon"])
    prop.precise_location.push(gp, "user")
    return {"status": "ok"}


@api_router.post("/seed")
async def seed_properties():
    from houses.nodes.bootstrap import seed_registry_from_sheet

    count = seed_registry_from_sheet()
    return {"seeded": count, "total": len(_registry)}


@api_router.get("/settings")
async def get_settings():
    svc = get_services()
    return {
        "persons": await svc.persons_source.to_json(),
        "financial": await svc.financial_source.to_json(),
        "commute_thresholds": await svc.commute_thresholds_source.to_json(),
    }


@api_router.get("/settings/decomposed")
async def get_settings_decomposed():
    svc = get_services()
    return {
        "persons": await svc.persons_source.to_json(),
        "financial": await svc.financial_source.to_json(),
        "commute_thresholds": await svc.commute_thresholds_source.to_json(),
    }


@api_router.patch("/settings/persons")
async def patch_persons(body: list = Body()):  # noqa: B008
    get_services().persons_source.push(body, "user")
    return {"status": "ok"}


@api_router.patch("/settings/financial")
async def patch_financial(body: dict):
    get_services().financial_source.push(body, "user")
    return {"status": "ok"}
