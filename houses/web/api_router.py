from __future__ import annotations

import contextlib
import logging

from fastapi import APIRouter, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field, field_validator

from houses.geo import GeoPoint
from houses.model.domain import (
    Person,
    PlaceOfInterest,
    effective_acceptable_modes,
    effective_editable_by,
    effective_selling_home,
)
from houses.property_registry import get_property as get_registry_property
from houses.property_registry import list_properties as list_registry_properties
from houses.services_provider import get_services
from houses.web.auth import get_session_user

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/api")


@api_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    from itsdangerous import BadSignature, SignatureExpired

    from houses.web.auth import _SESSION_MAX_AGE, _get_serializer

    # Extract session cookie from WebSocket headers — Starlette's Request
    # requires an HTTP scope and can't be constructed from a websocket scope.
    cookies_str = ""
    for key, value in websocket.headers.items():
        if key.lower() == "cookie":
            cookies_str = value
            break

    session = None
    for part in cookies_str.split(";"):
        part = part.strip()
        if part.startswith("session="):
            cookie_val = part[len("session=") :]
            with contextlib.suppress(BadSignature, SignatureExpired):
                session = _get_serializer().loads(
                    cookie_val,
                    max_age=int(_SESSION_MAX_AGE.total_seconds()),
                )
            break

    if not session or not session.get("email"):
        await websocket.close(code=4001)
        return

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
        walk = ps.get("walk")
        if isinstance(walk, dict):
            val = walk.get("value")
            if val is not None:
                score += _walk_score(int(val))
        elif walk is not None:
            score += _walk_score(walk)
    ss = s.get("schools", {}).get("secondary", {}).get("school", {}).get("value", {})
    if ss:
        score += _ofsted_score(ss.get("ofsted"))
        walk = ss.get("walk")
        if isinstance(walk, dict):
            val = walk.get("value")
            if val is not None:
                score += _walk_score(int(val))
        elif walk is not None:
            score += _walk_score(walk)
    walk_val = s.get("walkability", {})
    if isinstance(walk_val, dict):
        wv = walk_val.get("value")
        if isinstance(wv, dict):
            wt = wv.get("walk_to_town")
            if isinstance(wt, dict):
                val = wt.get("value")
                if val is not None:
                    score += _walk_score(int(val))
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


@api_router.get("/properties/{rid}/comments")
async def get_property_comments(rid: str):
    """Return all comments for a property.

    Reads from the comments table only — old-style sheet comments are
    migrated at deployment time via ``tools/migrate_comments.py``.
    """
    from houses.comments import get_comments

    # Validate the property exists
    prop = get_registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return get_comments(rid)


class CommentBody(BaseModel):
    """Validated request body for posting a comment.
    Person is determined server-side from the session, not from this body.
    """

    text: str = Field(min_length=1, max_length=5000)

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("text must not be whitespace-only")
        return stripped


@api_router.post("/properties/{rid}/comments")
async def add_property_comment(rid: str, body: CommentBody, request: Request):
    """Add a comment for a property.

    Person is determined from the authenticated session, with optional
    X-Impersonate-Person header for superusers.
    """
    from houses.comments import add_comment
    from houses.services_provider import get_services
    from houses.web.auth import get_session_user

    prop = get_registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")

    session_user = get_session_user(request)
    if not session_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    svc = get_services()

    impersonate = request.headers.get("X-Impersonate-Person", "")
    if impersonate:
        if not session_user.get("is_superuser"):
            raise HTTPException(status_code=403, detail="Only superusers can impersonate")
        if not impersonate.strip():
            raise HTTPException(status_code=400, detail="Impersonation person name must not be empty")
        person = impersonate
    else:
        folded_email = session_user.get("email", "").casefold()
        persons_attempt = svc.persons_source.latest_attempt()
        person = ""
        if persons_attempt.succeeded:
            for p in persons_attempt.value_or_none() or []:
                if isinstance(p, dict):
                    pe = p.get("email")
                    if pe is not None and pe.casefold() == folded_email:
                        person = p.get("name", "")
                        break
                elif hasattr(p, "email") and p.email is not None and p.email.casefold() == folded_email:
                    person = getattr(p, "name", "")
                    break
        if not person:
            raise HTTPException(
                status_code=400,
                detail="Your account is not linked to a person in settings",
            )

    return add_comment(rid, person, body.text)


@api_router.get("/settings")
async def get_settings(request: Request):
    svc = get_services()
    persons_json = await svc.persons_source.to_json()
    # Enrich each person with the EFFECTIVE per-POI modes, the effective
    # guardian list, and the session-aware editable_by_me flag.  The
    # server decides ownership; the UI only renders it.
    attempt = svc.persons_source.latest_attempt()
    persons = [p for p in (attempt.value_or_none() or []) if isinstance(p, Person)]
    session_user = get_session_user(request)
    session_name = _session_person_name(session_user, persons)
    dumped = persons_json.get("value")
    if isinstance(dumped, list):
        # match serialized entries to Person models BY NAME — a legacy
        # non-Person entry in the source must not crash the enrichment
        by_name = {p.name: p for p in persons}
        for item in dumped:
            if not isinstance(item, dict):
                continue
            person = by_name.get(item.get("name") or "")
            if person is None:
                continue
            editable_by = effective_editable_by(person, persons)
            item["editable_by"] = list(editable_by)
            item["editable_by_me"] = _can_edit_person(session_user, session_name, person, persons)
            item["selling_home"] = effective_selling_home(person)
            for poi_item, poi in zip(item.get("places_of_interest") or (), person.places_of_interest, strict=False):
                if isinstance(poi_item, dict):
                    poi_item["acceptable_modes"] = list(effective_acceptable_modes(poi))

    # The family deposit as ONE number (P4: the household is the unit, not
    # four person records): per person, sale proceeds − remaining mortgage +
    # extra money; plus the household total.  Computed server-side from the
    # settings inputs — the client never derives money from parts.  The
    # toggle gate matches EquityTotalNode: a person not selling a home
    # contributes cash only (P7).
    from decimal import Decimal as _Decimal

    from money import Money as _Money

    deposit_persons: dict[str, dict] = {}
    deposit_total = _Money("0", "GBP")
    deposit_lines: list[dict] = []
    for person in persons:
        sale = person.home_sale_price.amount
        mortgage = person.outstanding_mortgage.amount
        cash = person.cash_contribution.amount
        if effective_selling_home(person):
            value = max(_Decimal("0"), sale - mortgage) + cash
            line = (
                f"£{sale:,.2f} sale − £{mortgage:,.2f} mortgage + £{cash:,.2f} cash "
                f"= £{value:,.2f}"
            )
        else:
            value = cash
            line = f"£0 home + £{cash:,.2f} cash = £{value:,.2f}"
        deposit_persons[person.name] = {"amount": f"{value:.2f}", "currency": "GBP"}
        deposit_total = deposit_total + _Money(str(value), "GBP")
        deposit_lines.append({"label": person.name, "value": line})

    return {
        "persons": persons_json,
        "financial": await svc.financial_source.to_json(),
        "commute_thresholds": await svc.commute_thresholds_source.to_json(),
        "household_deposit": {
            "total": {"amount": f"{deposit_total.amount:.2f}", "currency": "GBP"},
            "persons": deposit_persons,
            "provenance": {
                "label": "Household Deposit",
                "value": f"£{deposit_total.amount:,.2f}",
                "sourceType": "calc",
                "formula": {"lines": deposit_lines, "result": f"£{deposit_total.amount:,.2f}"},
            },
        },
    }


def _session_person_name(session_user: dict | None, persons: list) -> str:
    """The session user's Person name (email match), or "" when unlinked."""
    if not session_user:
        return ""
    folded = session_user.get("email", "").casefold()
    for p in persons:
        email = getattr(p, "email", "")
        if email and email.casefold() == folded:
            return getattr(p, "name", "")
    return ""


def _can_edit_person(session_user: dict | None, session_name: str, person, persons: list) -> bool:
    """Server-side ownership check — the UI never decides this."""
    if not session_user:
        return False
    if session_user.get("is_superuser"):
        return True
    if not session_name:
        return False
    return session_name == person.name or session_name in effective_editable_by(person, persons)


_PERSON_MONEY_FIELDS = {"home_sale_price", "outstanding_mortgage", "cash_contribution", "life_insurance_monthly"}


def _person_from_dict(d: dict, target: Person) -> Person:
    """MERGE an API dict into an existing Person — never replace.

    Only the fields present in the body change; every unmentioned field
    keeps the target's value.  Replace semantics silently reset real data
    (emails, walk penalties, flags) whenever a client sends a partial
    body — that is exactly how the family emails were wiped.
    """
    from dataclasses import replace

    from money import Money as _Money
    from pint import Quantity as _Quantity

    def _money(v):
        """Validate the money shape: a number or {amount, currency}.  A
        malformed value must raise (→ 400) — storing it would poison every
        downstream read (.amount crashes) and freeze the equity cascade."""
        if isinstance(v, (int, float)):
            return _Money(str(v), "GBP")
        if isinstance(v, dict):
            if "amount" not in v:
                raise ValueError(f"money value missing 'amount': {v!r}")
            try:
                return _Money(v["amount"], v.get("currency", "GBP"))
            except Exception as e:
                raise ValueError(f"invalid money value {v!r}: {e}") from e
        raise ValueError(f"invalid money value {v!r} — expected a number or {{'amount': ...}}")

    def _penalty(v):
        """Validate bus_walk_penalty: the {value, unit} serialization."""
        if not isinstance(v, dict) or "value" not in v or "unit" not in v:
            raise ValueError(f"invalid walk penalty {v!r} — expected {{'value': ..., 'unit': ...}}")
        try:
            return _Quantity(v["value"], v["unit"])
        except Exception as e:
            raise ValueError(f"invalid walk penalty {v!r}: {e}") from e

    updates = {k: v for k, v in d.items() if k != "thresholds"}
    for f in _PERSON_MONEY_FIELDS:
        if f in updates:
            updates[f] = _money(updates[f])
    if "bus_walk_penalty" in updates:
        updates["bus_walk_penalty"] = _penalty(updates["bus_walk_penalty"])
    if "editable_by" in updates and updates["editable_by"] is not None:
        updates["editable_by"] = tuple(updates["editable_by"])
    pois = updates.get("places_of_interest")
    if isinstance(pois, list):
        normalized = []
        for poi in pois:
            if isinstance(poi, dict):
                modes = poi.get("acceptable_modes")
                if modes is not None:
                    poi = {**poi, "acceptable_modes": tuple(modes)}
                normalized.append(PlaceOfInterest(**poi))
            else:
                normalized.append(poi)
        updates["places_of_interest"] = tuple(normalized)
    return replace(target, **updates)


@api_router.patch("/settings/person/{name}")
async def patch_person(name: str, body: dict, request: Request):
    """Update one person's settings — own person / superuser / guardian.

    Replaces the whole-list PUT: the server decides who may edit whom,
    never the UI.  ``body`` is the full person record (the old PUT item)
    plus an optional ``thresholds`` dict for the person's commute
    thresholds, saved to the separate thresholds source under the same
    ownership rule.
    """
    from fastapi import HTTPException

    session_user = get_session_user(request)
    if not session_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    svc = get_services()
    persons = list(svc.persons_source.latest_attempt().value_or_none() or [])
    target = next((p for p in persons if getattr(p, "name", "") == name), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"No person named {name!r}")

    session_name = _session_person_name(session_user, persons)
    if not _can_edit_person(session_user, session_name, target, persons):
        raise HTTPException(
            status_code=403,
            detail="You can only edit your own settings (or a child's, if you are their guardian)",
        )

    if not session_user.get("is_superuser"):
        # identity/privilege fields are superuser-only: editing your own
        # record must not escalate to superuser, hijack the email link,
        # rename yourself onto another person's ownership key (name IS the
        # authz identity), or flip is_child (which changes guardianship)
        body = {
            **body,
            "name": target.name,
            "is_child": target.is_child,
            "is_superuser": target.is_superuser,
            "email": target.email,
            "editable_by": target.editable_by,
        }

    try:
        updated = [_person_from_dict(body, target) if p is target else p for p in persons]
        svc.persons_source.push(updated, "user")
    except (ValueError, TypeError) as e:
        # malformed client input is a CLIENT error (400), never a 500
        raise HTTPException(status_code=400, detail=str(e)) from e

    thresholds = body.get("thresholds")
    if isinstance(thresholds, dict):
        current = dict(svc.commute_thresholds_source.latest_attempt().value_or_none() or {})
        current[name] = thresholds
        svc.commute_thresholds_source.push(current, "user")

    return {"status": "ok"}


@api_router.patch("/settings/financial")
async def patch_financial(body: dict):
    from houses.nodes.settings_node import API_KEY_TO_NODE

    svc = get_services()
    for api_key, value in body.items():
        node_id = API_KEY_TO_NODE.get(api_key)
        if node_id is not None and node_id in svc.setting_nodes:
            svc.setting_nodes[node_id].push(value, "user")
    return {"status": "ok"}


@api_router.patch("/properties/{rid}/rental-income")
async def patch_rental_income(
    rid: str,
    body: dict,
):
    """Update the monthly rental income for a property.

    Body: {"value": 1200} or {"value": null} to clear.
    """
    from houses.property_registry import get_property

    prop = get_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")

    value = body.get("value")
    if value is not None and not isinstance(value, (int, float)):
        raise HTTPException(
            status_code=400,
            detail="value must be a number",
        )

    from money import Money as _Money

    prop.rental_income.push(
        _Money(str(value), "GBP") if value is not None else _Money("0", "GBP"),
        "user",
    )
    return {"status": "ok"}


@api_router.patch("/properties/{rid}/works-estimate")
async def patch_works_estimate(
    rid: str,
    body: dict,
):
    """Update the works estimate for a person on this property.

    Body: {"person": "Ashby", "value": 15000}
    """
    from houses.property_registry import get_property

    prop = get_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")

    person_name = body.get("person", "")
    if not person_name:
        raise HTTPException(status_code=400, detail="person is required")

    # Validate the person exists in the current persons configuration
    from houses.services_provider import get_services as _get_svc

    _pa = _get_svc().persons_source.latest_attempt()
    if _pa.succeeded and _pa.value_or_none():
        _names = {
            getattr(p, "name", None) or (p.get("name") if isinstance(p, dict) else None) for p in _pa.value_or_none()
        }
        if person_name not in _names:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown person: {person_name}",
            )

    value = body.get("value")
    if value is not None and not isinstance(value, (int, float)):
        raise HTTPException(
            status_code=400,
            detail="value must be a number",
        )

    current = prop.works_estimates.latest_attempt().value_or_none() or {}
    # Store as Money — the Money rule applies to all monetary values.
    from money import Money

    current[person_name] = Money(str(value), "GBP") if value is not None else None
    prop.works_estimates.push(current, "user")

    return {"status": "ok"}


@api_router.get("/persons")
async def list_persons():
    """Return the list of persons with non-empty email addresses.

    Used by the frontend superuser impersonation dropdown.
    Requires authentication via the /api/ middleware.
    """
    svc = get_services()
    persons_attempt = svc.persons_source.latest_attempt()
    result: list[dict[str, str]] = []
    if persons_attempt.succeeded:
        for p in persons_attempt.value_or_none() or []:
            if isinstance(p, dict):
                email = p.get("email", "")
                if email:
                    result.append({"name": p.get("name", ""), "email": email})
            elif hasattr(p, "email") and p.email:
                result.append({"name": getattr(p, "name", ""), "email": p.email})
    return {"persons": result}


@api_router.get("/debug/scheduler")
async def debug_scheduler():
    """Snapshot the scheduler's pending work — read-only, never drains.

    The background processor drains the queue automatically; this endpoint
    only reports what is queued right now.  (A manual get/put drain loop is
    an infinite loop — re-putting before the empty check — and blocks the
    event loop, wedging the server.)
    """
    from dag.scheduler import AsyncQueueScheduler as _AsyncQueueScheduler
    from dag.scheduler import _get_scheduler

    sched = _get_scheduler()
    if not isinstance(sched, _AsyncQueueScheduler):
        return {"type": type(sched).__name__, "error": "not AsyncQueueScheduler"}

    # _scheduled: node_id -> QueueEvent, one entry per queued node (the
    # queue itself is drained by the processor — never touch it here)
    queue_snapshot = [
        {"node_id": node_id, "scheduled_at": event.scheduled_at}
        for node_id, event in list(sched._scheduled.items())[:500]
    ]

    return {
        "queue_size": sched._queue.qsize(),
        "scheduled_count": len(sched._scheduled),
        "wakeup_set": sched._wakeup.is_set(),
        "queue": queue_snapshot,
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
