from __future__ import annotations

import contextlib
import gc
import logging
import typing
from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal as _Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from itsdangerous import BadSignature, SignatureExpired
from money import Money
from pint import Quantity as _Quantity
from pydantic import BaseModel, Field, field_validator

import dag.scheduler
from dag.evaluate import evaluate
from dag.scheduler import AsyncQueueScheduler, flush_processor
from houses.comments import add_comment, get_comments
from houses.geopoint import GeoPoint
from houses.map_layers import DRIVE_PATH, INTERSECTION_PATH, UNION_PATH, isochrone_layers
from houses.model.domain import (
    HomeCoOwner,
    Person,
    PlaceOfInterest,
    effective_acceptable_modes,
    effective_editable_by,
    effective_selling_home,
    home_equity_contributions,
)
from houses.nodes.settings_node import API_KEY_TO_NODE, aggregate_dict
from houses.services_provider import get_services
from houses.web.auth import SESSION_MAX_AGE, effective_session_user, get_serializer
from houses.web.broadcaster import register_client


def _registry_property(rid: str):
    """The live PropertyNodes for *rid* from the request-scoped registry."""
    return get_services().property_registry.get(rid)


def _registry_rids() -> list[str]:
    """All registered property IDs."""
    return get_services().property_registry.list_properties()

GOOD_COMMUTE_MIN = 30
BRACKNELL_WARN_COMMUTE_MIN = 60
STANDARD_GOOD_COMMUTE_MIN = 45
STANDARD_WARN_COMMUTE_MIN = 75
GOOD_WALK_MIN = 15
WARN_WALK_MIN = 30
TOTAL_SHARE_PERCENT = 100
MIN_SHARE_PERCENT = 1
MAX_SHARE_PERCENT = 100
TOP_TYPES_LIMIT = 30

logger = logging.getLogger(__name__)
@dataclass(frozen=True)
class DepositBreakdown:
    """(persons, total, lines) from _deposit_breakdown — named so callers
    read the fields by meaning, not position."""

    persons: dict
    total: Money
    lines: list[dict]


@dataclass(frozen=True)
class IsochronePaths:
    """The three committed isochrone artifact paths — the DI seam shape
    for the /map/isochrones endpoint."""

    union: Path
    drive: Path
    intersection: Path



api_router = APIRouter(prefix="/api")


@api_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
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
                session = get_serializer().loads(
                    cookie_val,
                    max_age=int(SESSION_MAX_AGE.total_seconds()),
                )
            break

    if not session or not session.get("email"):
        await websocket.close(code=4001)
        return

    await register_client(websocket)


def _merge_what_if_persons(updates: list, current: list) -> list:
    """Merge per-person update dicts into the current persons.

    Unmentioned persons keep their current values — the what-if only
    changes what the client edited.  Malformed input raises (→ 4xx).
    """
    by_name = {p.name: p for p in current}
    merged: list = []
    merged_names: set[str] = set()
    for d in updates:
        if not isinstance(d, dict) or not d.get("name"):
            raise HTTPException(status_code=422, detail="each person update needs a name")
        target = by_name.get(d["name"])
        if target is None:
            raise HTTPException(status_code=422, detail=f"unknown person {d['name']!r}")
        try:
            merged.append(_person_from_dict(d, target))
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        merged_names.add(d["name"])
    merged.extend(p for p in current if p.name not in merged_names)
    return merged


async def _what_if_results(merged: list):
    """Evaluate every registry property's group_monthly_cost under the
    merged persons; skipped properties are simply absent from the map."""
    results: dict[str, dict] = {}
    for rid in _registry_rids():
        prop = _registry_property(rid)
# lucidlint: ignore special-case sentinel handling is the contract here
        if prop is None:
            continue
        group_node = getattr(prop, "group_monthly_cost", None)
        if group_node is None:
            continue
        attempts = await evaluate(group_node, overrides={"persons": merged})
        group_att = attempts[group_node._id]
        if group_att.succeeded and group_att.value is not None:
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            results[rid] = {"succeeded": True, "group": group_att.value_or_none()}
        elif group_att.impossible:
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            results[rid] = {"succeeded": False, "error": group_att.error}
        else:
            results[rid] = {"succeeded": False, "error": "pending"}
    return results


@api_router.post("/what-if")
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def post_what_if(body: dict):
    """Pure what-if: evaluate every property's total monthly cost under
    candidate person settings (Part D).

    The body carries per-person updates in the same shape as
    ``PATCH /settings/person/{name}``; each is merged into the current
    person. Nothing is persisted — the DAG is evaluated with the
    candidate values staged and discarded (``dag.evaluate``), so the
    real scheduler, database, and node state are untouched.

    Response: ``{"results": {rid: {"succeeded": bool,
    "monthly_total": {"value": {"amount", "currency"}, "stddev"} | None,
    "error"?: str}}}``.
    """
    updates = body.get("persons")
    if not isinstance(updates, list) or not updates:
        raise HTTPException(status_code=422, detail="persons required")

    svc = get_services()
    current = list(svc.persons_source.latest_attempt().value_or_none() or [])
    merged = _merge_what_if_persons(updates, current)
    return {"results": await _what_if_results(merged)}


@api_router.get("/properties/{rid}/staleness")
async def staleness_check(rid: str, nodes: str = ""):
    """Check which DAG nodes are stale for a given property.

    Returns ``{"rid": str, "nodes": {node_id: bool, ...}, "fresh": bool}``.
    """
    prop = _registry_property(rid)
    if prop is None:
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {"rid": rid, "nodes": stale_map, "fresh": fresh}


def _commute_score(minutes: int | None, bracknell: bool = False) -> int:
    """2 for a good commute, 1 for acceptable, -1 for poor, 0 unknown."""
    if minutes is None:
        return 0
    if bracknell:
        return 2 if minutes < GOOD_COMMUTE_MIN else (1 if minutes <= BRACKNELL_WARN_COMMUTE_MIN else -1)
    return 2 if minutes < STANDARD_GOOD_COMMUTE_MIN else (1 if minutes <= STANDARD_WARN_COMMUTE_MIN else -1)


def _ofsted_score(rating: str | None) -> int:
    """Outstanding=2, Good=1, Requires Improvement/Inadequate=-1, else 0."""
    if rating == "Outstanding":
        return 2
    if rating == "Good":
        return 1
    if rating in ("Requires Improvement", "Inadequate"):
        return -1
    return 0


def _walk_score(minutes: int | None) -> int:
    """2 for a quick walk, 1 for acceptable, -1 for a slog, 0 unknown."""
    if minutes is None:
        return 0
    return 2 if minutes < GOOD_WALK_MIN else (1 if minutes <= WARN_WALK_MIN else -1)


def _walk_metric_score(walk: object) -> int:
    """Score one school's walk metric — a {value: minutes} dict or a bare number."""
    if isinstance(walk, dict):
        val = walk.get("value")
        if val is not None:
            return _walk_score(int(val))
        return 0
    if isinstance(walk, (int, float)):
        return _walk_score(int(walk))
    return 0


def _school_score(school_value: object) -> int:
    """Ofsted + walk scores for one school entry's value dict."""
    if not isinstance(school_value, dict) or not school_value:
        return 0
    return _ofsted_score(school_value.get("ofsted")) + _walk_metric_score(school_value.get("walk"))


def _walkability_score(walk_val: object) -> int:
    """The walk_to_town metric buried in walkability → walk score."""
    if not isinstance(walk_val, dict):
        return 0
    wv = walk_val.get("value")
    if not isinstance(wv, dict):
        return 0
    wt = wv.get("walk_to_town")
    if not isinstance(wt, dict):
        return 0
    val = wt.get("value")
    if val is None:
        return 0
    return _walk_score(int(val))


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _score_from_summary(s: dict) -> int:
    """Compute card score matching old ``card_data`` formula:
    green=2, orange=1, red=-1, muted=0, summed across 8 metrics.
    """
    score = 0
    for key, cd in s.get("commutes", {}).items():
        c = cd.get("commute", {})
        dur = c.get("value", {}).get("duration", {}).get("value") if c.get("status") == "succeeded" else None
        if dur is not None:
            score += _commute_score(dur, bracknell="Bracknell" in key)
    score += _school_score(s.get("schools", {}).get("primary", {}).get("school", {}).get("value", {}))
    score += _school_score(s.get("schools", {}).get("secondary", {}).get("school", {}).get("value", {}))
    score += _walkability_score(s.get("walkability", {}))
    return score


@api_router.get("/properties/all")
async def get_all_properties():
    results: dict[str, dict] = {}
    for rid in _registry_rids():
        prop = _registry_property(rid)
        if prop is None:
            continue
        results[rid] = await prop.to_json_summary()
    scored = sorted(results.items(), key=lambda kv: _score_from_summary(kv[1]), reverse=True)
    return dict(scored)


@api_router.get("/properties/current-homes")
async def list_current_homes():
    """The family's CURRENT house(s) — properties marked status=current —
    so a person can link their settings home fields to the right one."""
    result: list[dict[str, str]] = []
    for rid in _registry_rids():
        prop = _registry_property(rid)
        if prop is None:
            continue
        status = prop.comment_status.latest_attempt()
        if not status.succeeded or (status.value_or_none() or "").strip().lower() != "current":
            continue
        att = prop.best_address.latest_attempt()
        address = str(att.value_or_none()) if att.succeeded and att.value_or_none() else ""
        result.append({"rid": str(rid), "address": address})
    return {"homes": result}


@api_router.get("/properties/{rid}")
async def get_property(rid: str):
    prop = _registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    return await prop.to_json()


@api_router.get("/properties/{rid}/detail")
async def get_property_detail(rid: str):
    prop = _registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    return await prop.to_json_detail()


@api_router.patch("/properties/{rid}/address")
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def patch_address(rid: str, body: dict):
    prop = _registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")

    prop.corrected_address.push(body.get("address", ""), "user")
    # Recompute before responding — the frontend refetches the detail
    # immediately, and the background scheduler would race that request.
    await prop.best_address.refresh()
    # Drain the downstream cascade (council tax, EPC, geocode, commutes,
    # group cost) so the response — and the immediate refetch — reflect
    # the recomputed state, not a mid-cascade one (same pattern as
    # /admin/regenerate).
    await flush_processor()
    return {"status": "ok"}


@api_router.patch("/properties/{rid}/location")
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def patch_location(rid: str, body: dict):
    prop = _registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    lat = body.get("lat")
    lon = body.get("lon")
    if lat is None or lon is None:
        raise HTTPException(status_code=422, detail="lat and lon are required")
    gp = GeoPoint(lat=lat, lon=lon)
    prop.precise_location.push(gp, "user")
    # Recompute before responding — same race as the address PATCH.
    await prop.best_location.refresh()
    await flush_processor()
    return {"status": "ok"}


@api_router.patch("/properties/{rid}/council-tax")
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def patch_council_tax(rid: str, body: dict):
    """Set the council-tax apportionment for a property.

    ``main_payers`` — who pays a share of the MAIN house's council tax
    (they split it equally; empty = all adults, the default headcount
    split).  ``annexe_payers`` — who pays the ANNEXE's council tax.
    ``ignored`` — the detected second dwelling is unrelated; hide it and
    exclude its costs.
    """
    prop = _registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")

    def _names(value) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
            raise HTTPException(status_code=422, detail="payers must be a list of person names")
        return value

    # Validate the ENTIRE body before pushing anything — a partial write
    # on a 422 leaves the property in a half-updated state (review).
    main_payers = _names(body["main_payers"]) if "main_payers" in body else None
    annexe_payers = _names(body["annexe_payers"]) if "annexe_payers" in body else None
    if "ignored" in body and not isinstance(body["ignored"], bool):
        raise HTTPException(status_code=422, detail="ignored must be a boolean")
    ignored = body.get("ignored")

    if main_payers is not None:
        prop.council_tax_payers.push(main_payers, "user")
    if annexe_payers is not None:
        prop.annexe_payers.push(annexe_payers, "user")
    if ignored is not None:
        prop.annexe_ignored.push(ignored, "user")
    await flush_processor()
    return {"status": "ok"}


@api_router.patch("/properties/{rid}/triage")
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def patch_triage(rid: str, body: dict):
    prop = _registry_property(rid)
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
    # Validate the property exists
    prop = _registry_property(rid)
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


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _comment_person(request: Request, session_user: dict, svc) -> str:
    """Resolve the comment author — impersonation header for superusers,
    else the session email's linked person in settings."""
    impersonate = request.headers.get("X-Impersonate-Person", "")
    if impersonate:
        if not session_user.get("is_superuser"):
            raise HTTPException(status_code=403, detail="Only superusers can impersonate")
        if not impersonate.strip():
            raise HTTPException(status_code=400, detail="Impersonation person name must not be empty")
        return impersonate
    folded_email = session_user.get("email", "").casefold()
    persons_attempt = svc.persons_source.latest_attempt()
    if persons_attempt.succeeded:
        for p in persons_attempt.value_or_none() or []:
            name = ""
            if isinstance(p, dict):
                pe = p.get("email")
                if pe is not None and pe.casefold() == folded_email:
                    name = p.get("name", "")
            elif hasattr(p, "email") and p.email is not None and p.email.casefold() == folded_email:
                name = getattr(p, "name", "")
            if name:
                return name
    raise HTTPException(
        status_code=400,
        detail="Your account is not linked to a person in settings",
    )


@api_router.post("/properties/{rid}/comments")
async def add_property_comment(rid: str, body: CommentBody, request: Request):
    """Add a comment for a property.

    Person is determined from the authenticated session, with optional
    X-Impersonate-Person header for superusers.
    """
    prop = _registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")

    session_user = effective_session_user(request)
    if not session_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    svc = get_services()
    person = _comment_person(request, session_user, svc)
    return add_comment(rid, person, body.text)
def _home_property_address(person) -> str:
    """First street line of the linked house's best address; '' when unset."""
    linked_rid = getattr(person, "home_property_rid", "")
    if not linked_rid:
        return ""
    prop = _registry_property(linked_rid)
    if prop is None:
        return ""
    att = prop.best_address.latest_attempt()
    if not (att.succeeded and att.value_or_none()):
        return ""
    return str(att.value_or_none()).split("\n")[0].split(",")[0]


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _enrich_persons(dumped: object, persons: list, session_user: dict | None, session_name: str) -> None:
    """Enrich serialized persons with the EFFECTIVE per-POI modes, the
    effective guardian list, the session-aware editable_by_me flag, and
    the linked-house address.  The server decides ownership; the UI only
    renders it.  Entries are matched to Person models BY NAME — a legacy
    non-Person entry in the source must not crash the enrichment."""
    if not isinstance(dumped, list):
        return
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
        addr = _home_property_address(person)
        if addr:
            item["home_property_address"] = addr


@api_router.get("/settings")
async def get_settings(request: Request):
    svc = get_services()
    persons_json = await svc.persons_source.to_json()
    attempt = svc.persons_source.latest_attempt()
    persons = [p for p in (attempt.value_or_none() or []) if isinstance(p, Person)]
    session_user = effective_session_user(request)
    session_name = _session_person_name(session_user, persons)
    _enrich_persons(persons_json.get("value"), persons, session_user, session_name)

    # The family deposit as ONE number (P4): per person, sale proceeds −
    # remaining mortgage + extra money, plus the household total —
    # computed server-side, never derived from parts by the client.
    breakdown = _deposit_breakdown(persons)
    deposit_persons, deposit_total, deposit_lines = breakdown.persons, breakdown.total, breakdown.lines

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {
        "persons": persons_json,
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        "financial": {"status": "succeeded", "value": aggregate_dict(svc.setting_nodes)},
        "commute_thresholds": await svc.commute_thresholds_source.to_json(),
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        "household_deposit": {
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            "total": {"amount": f"{deposit_total.amount:.2f}", "currency": "GBP"},
            "persons": deposit_persons,
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            "provenance": {
                "label": "Household Deposit",
                "value": f"£{deposit_total.amount:,.2f}",
                "sourceType": "calc",
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
                "formula": {"lines": deposit_lines, "result": f"£{deposit_total.amount:,.2f}"},
            },
        },
    }


def _deposit_breakdown(persons: list) -> DepositBreakdown:
    """Per-person deposit (distributed home equity + cash) and the
    household total. Home equity splits by co-owner shares; children
    never contribute. Pure — unit-testable without the request (P4)."""
    contributions = home_equity_contributions(persons)
    by_name = {p.name: p for p in persons if not p.is_child}
    deposit_persons: dict[str, dict] = {}
    deposit_total = Money(amount="0", currency="GBP")
    deposit_lines: list[dict] = []
    for name, person in by_name.items():
        cash = person.cash_contribution.amount
        home_share = contributions.get(name, _Decimal("0"))
        value = home_share + cash
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        deposit_persons[name] = {"amount": f"{value:.2f}", "currency": "GBP"}
        deposit_total = deposit_total + Money(str(value), "GBP")
        if home_share > 0 and effective_selling_home(person):
            gross = max(_Decimal("0"), person.home_sale_price.amount - person.outstanding_mortgage.amount)
            co_sum = sum(co.share for co in person.home_co_owners)
            if co_sum == 0:
                line = (
                    f"£{person.home_sale_price.amount:,.2f} sale − "
                    f"£{person.outstanding_mortgage.amount:,.2f} mortgage + "
                    f"£{cash:,.2f} cash = £{value:,.2f}"
                )
            else:
                holder_part = f"£{gross:,.2f} home ({TOTAL_SHARE_PERCENT - co_sum}% yours) + "
                line = f"{holder_part}£{home_share:,.2f} home share + £{cash:,.2f} cash = £{value:,.2f}"
        elif home_share > 0:
            # this person's share came from co-owning someone else's home
            source = ""
            for other in by_name.values():
                if other.name == name:
                    continue
                for co in other.home_co_owners:
                    if co.name == name:
                        gross_other = max(
                            _Decimal("0"),
                            other.home_sale_price.amount - other.outstanding_mortgage.amount,
                        )
                        source = f"{co.share}% of {other.name}'s home (£{gross_other:,.2f}) "
            line = f"{source}+ £{cash:,.2f} cash = £{value:,.2f}"
        else:
            line = f"£0 home + £{cash:,.2f} cash = £{value:,.2f}"
        deposit_lines.append({"label": name, "value": line})
    return DepositBreakdown(deposit_persons, deposit_total, deposit_lines)


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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
# Large house-purchase / deposit amounts are whole pounds — never pence
# (the UI enforces this too; this is the hard guarantee).
_WHOLE_POUND_FIELDS = {"home_sale_price", "outstanding_mortgage", "cash_contribution"}


def _parse_money(v: object, *, whole_pounds: bool = False, field: str = "amount") -> Money:
    """Validate the money shape: a number or {amount, currency}.  A
    malformed value must raise (→ 400) — storing it would poison every
    downstream read (.amount crashes) and freeze the equity cascade."""
    if isinstance(v, (int, float)):
        m = Money(str(v), "GBP")
    elif isinstance(v, dict):
        if "amount" not in v:
            raise ValueError(f"money value missing 'amount': {v!r}")
        try:
            m = Money(v["amount"], v.get("currency", "GBP"))
        # lucidlint: ignore broad-except money coercion re-raises as ValueError with the value
        except Exception as e:
            raise ValueError(f"invalid money value {v!r}: {e}") from e
    else:
        raise ValueError(f"invalid money value {v!r} — expected a number or {{'amount': ...}}")
    if whole_pounds and m.amount != m.amount.to_integral_value():
        raise ValueError(f"{field} must be a whole number of pounds — no pence")
    return m


def _parse_penalty(v: object) -> _Quantity:
    """Validate bus_walk_penalty: the {value, unit} serialization."""
    if not isinstance(v, dict) or "value" not in v or "unit" not in v:
        raise ValueError(f"invalid walk penalty {v!r} — expected {{'value': ..., 'unit': ...}}")
    try:
        return _Quantity(v["value"], v["unit"])
        # lucidlint: ignore broad-except boundary — any Quantity construction failure converts to ValueError (→ 400)
    except Exception as e:
        raise ValueError(f"invalid walk penalty {v!r}: {e}") from e


def _parse_petrol_mpg(mpg: object) -> int:
    """petrol_mpg must be a positive number — stored whole."""
    if not isinstance(mpg, (int, float)) or mpg <= 0:
        raise ValueError(f"petrol_mpg must be a positive number, got {mpg!r}")
    return int(mpg)


def _parse_co_owners(co: object) -> tuple[HomeCoOwner, ...]:
    """Validate home_co_owners: {name, whole-percent share} entries ≤ 100%."""
    if not isinstance(co, list):
        raise ValueError(f"home_co_owners must be a list, got {type(co).__name__}")
    parsed = []
    total_share = 0
    for item in co:
        if not isinstance(item, dict) or "name" not in item or "share" not in item:
            raise ValueError(f"invalid co-owner {item!r} — expected {{'name', 'share'}}")
        share = item["share"]
        if not isinstance(share, int) or not MIN_SHARE_PERCENT <= share <= MAX_SHARE_PERCENT:
            raise ValueError(f"co-owner share must be a whole percent 1-100, got {share!r}")
        parsed.append(HomeCoOwner(name=str(item["name"]), share=share))
        total_share += share
    if total_share > MAX_SHARE_PERCENT:
        raise ValueError(f"co-owner shares total {total_share}% — cannot exceed 100%")
    return tuple(parsed)


def _parse_home_property_rid(rid: object) -> str:
    """home_property_rid must be a string or null; null → ''."""
    if rid is not None and not isinstance(rid, str):
        raise ValueError("home_property_rid must be a string or null")
    return rid if isinstance(rid, str) else ""


def _parse_places_of_interest(pois: object) -> tuple:
    """Validate/normalize places_of_interest: dict entries become
    PlaceOfInterest models (modes tuple-ized), anything else passes
    through untouched."""
    if not isinstance(pois, list):
        raise ValueError(f"places_of_interest must be a list, got {type(pois).__name__}")
    normalized = []
    for poi in pois:
        if isinstance(poi, dict):
            modes = poi.get("acceptable_modes")
            if modes is not None:
                poi = {**poi, "acceptable_modes": tuple(modes)}
            normalized.append(PlaceOfInterest(**typing.cast(dict[str, Any], poi)))
        else:
            normalized.append(poi)
    return tuple(normalized)


def _person_from_dict(d: dict, target: Person) -> Person:
    """MERGE an API dict into an existing Person — never replace.

    Only the fields present in the body change; every unmentioned field
    keeps the target's value.  Replace semantics silently reset real data
    (emails, walk penalties, flags) whenever a client sends a partial
    body — that is exactly how the family emails were wiped.
    """
    updates = {k: v for k, v in d.items() if k != "thresholds"}
    for f in _PERSON_MONEY_FIELDS:
        if f in updates:
            updates[f] = _parse_money(updates[f], whole_pounds=f in _WHOLE_POUND_FIELDS, field=f)
    if "bus_walk_penalty" in updates:
        updates["bus_walk_penalty"] = _parse_penalty(updates["bus_walk_penalty"])
    if "petrol_mpg" in updates:
        updates["petrol_mpg"] = _parse_petrol_mpg(updates["petrol_mpg"])
    if "home_co_owners" in updates:
        updates["home_co_owners"] = _parse_co_owners(updates["home_co_owners"])
    if "home_property_rid" in updates:
        updates["home_property_rid"] = _parse_home_property_rid(updates["home_property_rid"])
    if "rent_paid_monthly" in updates:
        updates["rent_paid_monthly"] = _parse_money(updates["rent_paid_monthly"], field="rent_paid_monthly")
    if "editable_by" in updates and updates["editable_by"] is not None:
        updates["editable_by"] = tuple(updates["editable_by"])
    if "places_of_interest" in updates:
        updates["places_of_interest"] = _parse_places_of_interest(updates["places_of_interest"])
    return replace(target, **updates)


def _isochrone_paths() -> IsochronePaths:
    """Artifact paths for the isochrone layers — DI seam for tests."""
    return IsochronePaths(UNION_PATH, DRIVE_PATH, INTERSECTION_PATH)


@api_router.get("/map/isochrones")
async def get_isochrone_layers(paths: IsochronePaths = Depends(_isochrone_paths)):  # noqa: B008  # FastAPI DI convention
    """The isochrone polygons for the Map page (transit shed, drive sheds,
    all-commutes intersection) from the committed toolchain artifacts.

    Returns ``{"layers": [...]}``; empty layers when no artifacts exist.
    """
    union_path, drive_path, intersection_path = paths.union, paths.drive, paths.intersection
    return {
        "layers": isochrone_layers(
            union_path=union_path,
            drive_path=drive_path,
            intersection_path=intersection_path,
        )
    }


@api_router.patch("/settings/person/{name}")
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def patch_person(name: str, body: dict, request: Request):
    """Update one person's settings — own person / superuser / guardian.

    Replaces the whole-list PUT: the server decides who may edit whom,
    never the UI.  ``body`` is the full person record (the old PUT item)
    plus an optional ``thresholds`` dict for the person's commute
    thresholds, saved to the separate thresholds source under the same
    ownership rule.
    """
    session_user = effective_session_user(request)
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
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def patch_financial(body: dict):
    svc = get_services()
    for api_key, value in body.items():
        node_id = API_KEY_TO_NODE.get(api_key)
        if node_id is not None and node_id in svc.setting_nodes:
            svc.setting_nodes[node_id].push(value, "user")
    return {"status": "ok"}


@api_router.patch("/properties/{rid}/rental-income")
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def patch_rental_income(
    rid: str,
    body: dict,
):
    """Update the monthly rental income for a property.

    Body: {"value": 1200} or {"value": null} to clear.
    """
    prop = _registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")

    value = body.get("value")
    if value is not None and not isinstance(value, (int, float)):
        raise HTTPException(
            status_code=400,
            detail="value must be a number",
        )

    prop.rental_income.push(
        Money(str(value), "GBP") if value is not None else Money(amount="0", currency="GBP"),
        "user",
    )
    return {"status": "ok"}


def _validate_works_person(person_name: str) -> None:
    """400 guard: the person must exist in the current persons config."""
    if not person_name:
        raise HTTPException(status_code=400, detail="person is required")
    _pa = get_services().persons_source.latest_attempt()
    _pa_value = _pa.value_or_none()
    if _pa.succeeded and _pa_value:
        _names = {getattr(p, "name", None) or (p.get("name") if isinstance(p, dict) else None) for p in _pa_value}
        if person_name not in _names:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown person: {person_name}",
            )


def _validate_works_value(value: object) -> None:
    """400 guards on the estimate: numeric, and whole pounds only.

    Works estimates are large house-purchase amounts — pence fail fast
    (400), never silently rounded.
    """
    if value is not None and not isinstance(value, (int, float)):
        raise HTTPException(status_code=400, detail="value must be a number")
    if value is not None and value != int(value):
        raise HTTPException(status_code=400, detail="value must be a whole number of pounds — no pence")


@api_router.patch("/properties/{rid}/works-estimate")
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def patch_works_estimate(
    rid: str,
    body: dict,
):
    """Update the works estimate for a person on this property.

    Body: {"person": "Ashby", "value": 15000}
    """
    prop = _registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")

    person_name = body.get("person", "")
    _validate_works_person(person_name)
    value = body.get("value")
    _validate_works_value(value)

    current = prop.works_estimates.latest_attempt().value_or_none() or {}
    # Store as Money — the Money rule applies to all monetary values.
    current[person_name] = Money(str(value), "GBP") if value is not None else None
    prop.works_estimates.push(current, "user")

    return {"status": "ok"}


@api_router.get("/persons")
async def list_persons():
    """Return ALL persons (name, email when present, is_child).

    Used by the frontend superuser impersonation dropdown. The email
    filter is gone — callers that need only email-linked persons (or
    only adults) must filter themselves; the impersonate endpoint
    enforces the no-children rule server-side.
    """
    svc = get_services()
    persons_attempt = svc.persons_source.latest_attempt()
    result: list[dict[str, object]] = []
    if persons_attempt.succeeded:
        result = [
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            {"name": p.get("name", ""), "email": p.get("email", ""), "is_child": bool(p.get("is_child"))}
            if isinstance(p, dict)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            else {
                "name": getattr(p, "name", ""),
                "email": getattr(p, "email", ""),
                # lucidlint: ignore boolean-arg False is getattr's default, not a named flag
                "is_child": bool(getattr(p, "is_child", False)),
            }
            for p in persons_attempt.value_or_none() or []
        ]
    return {"persons": result}


@api_router.get("/debug/scheduler")
async def debug_scheduler():
    """Snapshot the scheduler's pending work — read-only, never drains.

    The background processor drains the queue automatically; this endpoint
    only reports what is queued right now.  (A manual get/put drain loop is
    an infinite loop — re-putting before the empty check — and blocks the
    event loop, wedging the server.)
    """
    sched = dag.scheduler.get_scheduler()
    if not isinstance(sched, AsyncQueueScheduler):
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        return {"type": type(sched).__name__, "error": "not AsyncQueueScheduler"}

    # _scheduled: node_id -> QueueEvent, one entry per queued node (the
    # queue itself is drained by the processor — never touch it here)
    queue_snapshot = [
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        {"node_id": node_id, "scheduled_at": event.scheduled_at}
        for node_id, event in list(sched._scheduled.items())[:500]
    ]

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {
        "queue_size": sched._queue.qsize(),
        "scheduled_count": len(sched._scheduled),
        "wakeup_set": sched._wakeup.is_set(),
        "queue": queue_snapshot,
    }


@api_router.get("/debug/memory")
async def debug_memory():
    """Count Python objects by type — helps diagnose memory leaks."""

    gc.collect()
    obj_counts = Counter(type(o).__name__ for o in gc.get_objects())
    top = obj_counts.most_common(TOP_TYPES_LIMIT)

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {
        "total_objects": sum(obj_counts.values()),
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        "top_types": [{"type": t, "count": c} for t, c in top],
    }

