from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException, WebSocket

from houses.geo import GeoPoint
from houses.nodes.bootstrap import seed_registry_from_sheet
from houses.property_registry import _registry
from houses.services_provider import get_services

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/api")


@api_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    from houses.web.broadcaster import register_client
    await register_client(websocket)


@api_router.get("/properties/{rid}/staleness")
async def staleness_check(rid: str, nodes: str = ""):
    """Check which DAG nodes are stale for a given property.

    Returns ``{"rid": str, "nodes": {node_id: bool, ...}, "fresh": bool}``.
    """
    from houses.property_registry import _registry

    prop = _registry.get(rid)
    if prop is None:
        return {"rid": rid, "nodes": {}, "fresh": False, "error": "property not found"}

    node_list = [n.strip() for n in nodes.split(",") if n.strip()]
    detail = await prop.to_json_detail()
    stale_map: dict[str, bool] = {}
    for nid in node_list:
        parts = nid.split("/", 1)
        if len(parts) == 1:
            val = detail.get(nid, {})
        else:
            val = detail
            for segment in parts:
                if isinstance(val, dict):
                    val = val.get(segment, {})
                else:
                    val = {}
                    break
        if isinstance(val, dict):
            stale_map[nid] = val.get("status") != "succeeded"
        else:
            stale_map[nid] = True

    fresh = not any(stale_map.values())
    return {"rid": rid, "nodes": stale_map, "fresh": fresh}


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
    lat = body.get("lat")
    lon = body.get("lon")
    if lat is None or lon is None:
        raise HTTPException(status_code=422, detail="lat and lon are required")
    gp = GeoPoint(lat=lat, lon=lon)
    prop.precise_location.push(gp, "user")
    return {"status": "ok"}


@api_router.post("/seed")
async def seed_properties():
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


@api_router.patch("/settings/persons")
async def patch_persons(body: list = Body()):  # noqa: B008
    from houses.model.domain import Person, PlaceOfInterest
    persons = [
        Person(**{k: (
            tuple(PlaceOfInterest(**poi) if isinstance(poi, dict) else poi
                  for poi in v) if k == "places_of_interest" else v
        ) for k, v in p.items()}) if isinstance(p, dict) else p
        for p in body
    ]
    get_services().persons_source.push(persons, "user")
    return {"status": "ok"}


@api_router.patch("/settings/financial")
async def patch_financial(body: dict):
    get_services().financial_source.push(body, "user")
    return {"status": "ok"}
