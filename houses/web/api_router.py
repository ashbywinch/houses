from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException, WebSocket

from houses.geo import GeoPoint
from houses.nodes.bootstrap import seed_registry_from_sheet
from houses.property_registry import get_property as get_registry_property
from houses.property_registry import list_properties as list_registry_properties
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
    prop = get_registry_property(rid)
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
    return {"properties": list_registry_properties()}


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
    for rid in list_registry_properties():
        prop = get_registry_property(rid)
        if prop is None:
            continue
        results[rid] = await prop.to_json_summary()
    scored = sorted(results.items(), key=lambda kv: _score_from_summary(kv[1]), reverse=True)
    return dict(scored)


@api_router.get("/properties/{rid}")
async def get_property(rid: str):
    prop = get_registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    return await prop.to_json()


@api_router.get("/properties/{rid}/detail")
async def get_property_detail(rid: str):
    prop = get_registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    return await prop.to_json_detail()


@api_router.patch("/properties/{rid}/address")
async def patch_address(rid: str, body: dict):
    prop = get_registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")

    prop.corrected_address.push(body.get("address", ""), "user")
    return {"status": "ok"}


@api_router.patch("/properties/{rid}/location")
async def patch_location(rid: str, body: dict):
    prop = get_registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    lat = body.get("lat")
    lon = body.get("lon")
    if lat is None or lon is None:
        raise HTTPException(status_code=422, detail="lat and lon are required")
    gp = GeoPoint(lat=lat, lon=lon)
    prop.precise_location.push(gp, "user")
    return {"status": "ok"}


@api_router.patch("/properties/{rid}/triage")
async def patch_triage(rid: str, body: dict):
    prop = get_registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    if "favourite" in body:
        prop.favourite.push(bool(body["favourite"]), "user")
    if "dismissed" in body:
        prop.dismissed.push(bool(body["dismissed"]), "user")
    if "is_viewed" in body:
        prop.is_viewed.push(bool(body["is_viewed"]), "user")
    if "user_notes" in body:
        prop.user_notes.push(str(body["user_notes"]), "user")
    if "triage_status" in body:
        prop.triage_status.push(str(body["triage_status"]), "user")
    return {"status": "ok"}


@api_router.post("/seed")
async def seed_properties():
    count = seed_registry_from_sheet()
    return {"seeded": count, "total": len(list_registry_properties())}


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
    # Validate deposit_equity — the Money validator rejects bare numbers
    # (int/float) but accepts {'amount': ..., 'currency': ...} dicts.
    # We also need to CONVERT the dict to Money here because Pydantic's
    # dataclass handling doesn't call the Money schema for Money|None
    # union fields, so dump_python() later would fail serializing a dict.
    from money import Money as _Money

    from houses.model.domain import Person, PlaceOfInterest

    def _validate_de(d: dict) -> dict:
        de = d.get("deposit_equity")
        if isinstance(de, (int, float)):
            from fastapi import HTTPException

            raise HTTPException(
                status_code=400,
                detail=f"deposit_equity must be a dict or null, got {type(de).__name__}: {de}",
            )
        if isinstance(de, dict):
            d["deposit_equity"] = _Money(de["amount"], de.get("currency", "GBP"))
        return d

    persons = []
    for p in body:
        if isinstance(p, dict):
            _validate_de(p)
            persons.append(
                Person(
                    **{
                        k: (
                            tuple(PlaceOfInterest(**poi) if isinstance(poi, dict) else poi for poi in v)
                            if k == "places_of_interest"
                            else v
                        )
                        for k, v in p.items()
                    }
                )
            )
        else:
            persons.append(p)
    try:
        get_services().persons_source.push(persons, "user")
    except (ValueError, TypeError) as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok"}

@api_router.patch("/settings/financial")
async def patch_financial(body: dict):
    get_services().financial_source.push(body, "user")
    return {"status": "ok"}

@api_router.get("/debug/scheduler")
async def debug_scheduler():
    """Dump the entire scheduler queue — for debugging stalled background processing."""
    from dag.derived_node import AsyncQueueScheduler as _AsyncQueueScheduler
    from dag.derived_node import _get_scheduler

    sched = _get_scheduler()
    if not isinstance(sched, _AsyncQueueScheduler):
        return {"type": type(sched).__name__, "error": "not AsyncQueueScheduler"}

    queue_snapshot = []
    while not sched._queue.empty():
        try:
            ev = sched._queue.get_nowait()
            queue_snapshot.append({
                "node_id": ev.node_id,
                "scheduled_at": ev.scheduled_at,
            })
            sched._queue.put_nowait(ev)
        except Exception:
            break

    return {
        "queue_size": len(queue_snapshot),
        "scheduled_count": len(sched._scheduled),
        "wakeup_set": sched._wakeup.is_set(),
        "queue": queue_snapshot[:500],
    }

@api_router.get("/debug/memory")
async def debug_memory():
    """Count Python objects by type — helps diagnose memory leaks."""
    import gc
    from collections import Counter

    gc.collect()
    obj_counts = Counter(type(o).__name__ for o in gc.get_objects())
    top = obj_counts.most_common(30)

    return {
        "total_objects": sum(obj_counts.values()),
        "top_types": [{"type": t, "count": c} for t, c in top],
    }
