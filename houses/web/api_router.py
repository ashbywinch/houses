from __future__ import annotations

import contextlib
import gc
import logging
import typing
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from itsdangerous import BadSignature, SignatureExpired
from money import Money
from pint import Quantity as _Quantity
from pydantic import (
    BaseModel,
    Field,
    field_validator,
)

import dag.scheduler
from dag.persistence import node_result_before
from dag.scheduler import AsyncRefreshQueue, run_on_processor
from houses.comments import add_comment, get_comments
from houses.geopoint import GeoPoint
from houses.map_layers import DRIVE_PATH, INTERSECTION_PATH, UNION_PATH, isochrone_layers
from houses.model.domain import (
    HomeCoOwner,
    Person,
    PlaceOfInterest,
    person_id_of,
)
from houses.nodes.commute import commute_band
from houses.nodes.property_nodes import PropertyNodes
from houses.nodes.property_summary import PropertySummary
from houses.nodes.property_view import PropertyView
from houses.nodes.settings_node import SettingsNode
from houses.scrape_queue import scrape_status_for_rid
from houses.services_provider import get_services
from houses.web.auth import (
    SESSION_MAX_AGE,
    SessionClaims,
    effective_session_user,
    get_serializer,
    person_key,
    resolve_person_identity,
)
from houses.web.broadcaster import register_client, stale_push_rids
from houses.web.monthly_delta import attach as attach_monthly_delta
from houses.web.settings_payload import SessionPersons, SettingsPayload, settings_payload
from serialization import SerializableAsDict, omit_when_none


def _registry_property(rid: str):
    """The live PropertyNodes for *rid* from the request-scoped registry."""
    return get_services().property_registry.get(rid)


def _registry_rids() -> list[str]:
    """All registered property IDs."""
    return get_services().property_registry.list_properties()


class _ApiPolicy:
    """The list/detail wire policies: walk-band minutes, share-percent sanity
    bounds, and the top-types limit — one owner (global-state gate)."""

    GOOD_WALK_MIN = 15
    WARN_WALK_MIN = 30
    MIN_SHARE_PERCENT = 1
    MAX_SHARE_PERCENT = 100
    TOP_TYPES_LIMIT = 30

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IsochronePaths:
    """The three committed isochrone artifact paths — the DI seam shape
    for the /map/isochrones endpoint."""

    union: Path
    drive: Path
    intersection: Path


api_router = APIRouter(prefix="/api")


class StalenessReport(SerializableAsDict):
    """Per-property node staleness as the endpoint reports it."""

    rid: str
    nodes: dict[str, bool]
    fresh: bool
    # ``error`` appears only when there is one — the frontend reads its
    # presence, not its value.
    error: str | None = Field(default=None, exclude_if=omit_when_none)


def _registered_properties() -> Iterable[PropertyNodes]:
    """Every registered property — the skip-absent case lives here once
    (list endpoints iterate; the per-endpoint None check is gone)."""
    for rid in _registry_rids():
        prop = _registry_property(rid)
        if prop is None:
            continue
        yield prop


def _require_property(rid: str) -> PropertyNodes:
    """The registered property or a 404 — one absent-case handling for every
    endpoint, instead of a per-endpoint None check."""
    prop = _registry_property(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    return prop





class _StatusOk(TypedDict):
    """The patch endpoints' {status: ok} envelope."""

    status: str


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
    by_key: dict[str, Person] = {}
    for p in current:
        by_key[person_key(p)] = p
        by_key[getattr(p, "name", "")] = p
    merged: list = []
    merged_keys: set[str] = set()
    for d in updates:
        if not isinstance(d, dict):
            raise HTTPException(status_code=422, detail="each person update needs a name")
        key = d.get("person_id") or d.get("name")
        if not key:
            raise HTTPException(status_code=422, detail="each person update needs a person_id")
        target = by_key.get(key)
        if target is None:
            raise HTTPException(status_code=422, detail=f"unknown person {key!r}")
        try:
            merged.append(_person_from_dict(d, target, merge_destinations=True))
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        merged_keys.add(person_key(target))
    merged.extend(p for p in current if person_key(p) not in merged_keys)
    return merged


def _require_family_member(request: Request) -> None:
    """A what-if changes the whole household's numbers: any signed-in
    family member (session linked to a person) or a superuser may apply
    or restore one."""
    session_user = effective_session_user(request)
    if not session_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    view = SessionPersons(
        persons=list(get_services().persons_source.latest_attempt().value_or_none() or []),
        session_user=session_user,
    )
    if not session_user.is_superuser and not view.session_name():
        raise HTTPException(status_code=403, detail="Not authorised")


@dataclass(frozen=True)
class _WhatIfApply:
    """The what-if apply request body: the person updates to merge."""

    persons: list

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> _WhatIfApply:
        people = raw.get("persons")
        return cls(persons=people if isinstance(people, list) else [])




@api_router.post("/what-if/apply")
async def what_if_apply(what_if_body: Mapping[str, Any], request: Request):
    """Apply what-if person values THROUGH THE DAG.

    The merged persons are written via the normal settings push, so the
    DAG recomputes everything downstream (totals, commute breakdown,
    deltas) and the broadcaster pushes every surface — there is no side
    evaluation to keep in sync.

    On the inactive->active transition the whatif_started_at marker is
    set: node_results is append-only history, so the pre-what-if persons
    attempt stays in the DAG and restore simply re-appends the attempt
    the marker points before. No numbers are copied anywhere.
    """
    _require_family_member(request)
    updates = _WhatIfApply.from_dict(what_if_body).persons
    if not isinstance(updates, list) or not updates:
        raise HTTPException(status_code=422, detail="persons required")
    svc = get_services()
    started = (svc.whatif_started_at.latest_attempt().value_or_none() or "").strip()
    if not started:
        # Mark the boundary BEFORE the scenario push: the persons attempt
        # latest before this instant is the restore reference.
        started = datetime.now(UTC).isoformat()
        svc.whatif_started_at.push(started, "what-if")

    current = list(svc.persons_source.latest_attempt().value_or_none() or [])
    merged = _merge_what_if_persons(updates, current)
    svc.persons_source.push(merged, "what-if")
    return {"active": True}



@api_router.post("/what-if/restore")
async def what_if_restore(request: Request):
    """End the what-if: re-append the pre-what-if persons attempt (the
    one the started-at marker points before) through the normal settings
    write, and clear the marker."""
    _require_family_member(request)
    svc = get_services()
    started = (svc.whatif_started_at.latest_attempt().value_or_none() or "").strip()
    if not started:
        raise HTTPException(status_code=409, detail="No what-if is active")

    row = node_result_before(svc.persons_source._id, started)
    if row is None or not isinstance(row.get("value"), list):
        raise HTTPException(status_code=409, detail="No pre-what-if persons attempt found to restore")

    persons = svc.persons_source._adapter.validate_python(row["value"])

    def _restore() -> None:
        svc.persons_source.push(persons, "what-if-restore")
        svc.whatif_started_at.push("", "what-if-restore")

    _restore()
    return {"active": False}



@api_router.get("/what-if/state")
async def what_if_state():
    """Whether what-if numbers are currently live (the started-at marker
    is set)."""
    started = (get_services().whatif_started_at.latest_attempt().value_or_none() or "").strip()
    return {"active": bool(started)}



@api_router.post("/what-if/accept")
async def what_if_accept(request: Request):
    """Accept the what-if: the scenario becomes the real numbers. The
    marker clears and the snapshot is discarded — restore can no longer
    reach the pre-what-if values."""
    _require_family_member(request)
    svc = get_services()
    started = (svc.whatif_started_at.latest_attempt().value_or_none() or "").strip()
    if not started:
        raise HTTPException(status_code=409, detail="No what-if is active")
    svc.whatif_started_at.push("", "what-if-accept")
    return {"active": False}




@api_router.get("/properties/{rid}/staleness", response_model=StalenessReport)
async def staleness_check(rid: str, nodes: str = ""):
    """Check which DAG nodes are stale for a given property.

    Returns ``{"rid": str, "nodes": {node_id: bool, ...}, "fresh": bool}``.
    """
    prop = _registry_property(rid)
    if prop is None:
        return StalenessReport(rid=rid, nodes={}, fresh=False, error="property not found")

    node_list = [n.strip() for n in nodes.split(",") if n.strip()]
    detail = (await prop.detail()).to_dict()
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
    return StalenessReport(rid=rid, nodes=stale_map, fresh=fresh)


def _commute_score(minutes: int | None, bracknell: bool = False) -> int:
    """2 for a good commute, 1 for acceptable, -1 for poor, 0 unknown."""
    return {"good": 2, "warn": 1, "bad": -1, "unknown": 0}[commute_band(minutes, bracknell)]


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
    return 2 if minutes < _ApiPolicy.GOOD_WALK_MIN else (1 if minutes <= _ApiPolicy.WARN_WALK_MIN else -1)


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


def _mapping_at(value: object, key: str) -> Mapping[str, Any] | None:
    """Unwrap one level of a nested wire dict: the mapping at *key*, else
    ``None`` — a missing level ends the walk without a guard per step."""
    if not isinstance(value, Mapping):
        return None
    nested = value.get(key)
    return nested if isinstance(nested, Mapping) else None


def _walkability_score(walk_val: object) -> int:
    """The walk_to_town metric buried in walkability → walk score."""
    walk_to_town = _mapping_at(_mapping_at(walk_val, "value"), "walk_to_town")
    if walk_to_town is None:
        return 0
    val = walk_to_town.get("value")
    if val is None:
        return 0
    return _walk_score(int(val))


def _score_from_summary(s: PropertySummary) -> int:
    """Compute card score matching old ``card_data`` formula:
    green=2, orange=1, red=-1, muted=0, summed across 8 metrics.
    """
    score = 0
    for key, cd in s.commutes.items():
        # An unpriced commute is a normal live state: the tolerant wrapper
        # serves succeeded with a null value when a destination's route
        # failed, so a broken route cannot blank the household figures.
        # Such an entry scores nothing — it must never take the page down.
        c = (cd or {}).get("commute") or {}
        value = c.get("value") or {}
        duration = value.get("duration") or {}
        dur = duration.get("value") if c.get("status") == "succeeded" else None
        if isinstance(dur, (int, float)):
            score += _commute_score(int(dur), bracknell="Bracknell" in key)
    score += _school_score(s.schools.get("primary", {}).get("school", {}).get("value", {}))
    score += _school_score(s.schools.get("secondary", {}).get("school", {}).get("value", {}))
    score += _walkability_score(s.walkability)
    return score


def _attach_scrape_state(summary: PropertySummary | PropertyView, rid: str) -> Mapping[str, Any]:
    """Attach the property's REAL scrape-queue state to its serialized
    record, when a job exists — the card's honest states come from here,
    never a fake client-side timer. The card's key set follows the record
    passed in (summary or property json), so callers that mutate the card
    take a copy; the returned object is the card's JSON object."""
    d = dict(summary.to_dict())
    status = scrape_status_for_rid(rid)
    if status is not None:
        d["scrape"] = status.to_dict()
    return d


@api_router.get("/properties/all")
async def get_all_properties():
    results: dict[str, dict] = {}
    scores: dict[str, int] = {}
    for prop in _registered_properties():
        rid = prop.rid
        summary = await prop.summary()
        wire = dict(_attach_scrape_state(summary, rid))
        await attach_monthly_delta(wire, rid)
        if rid in stale_push_rids():
            wire["push_stale"] = True
        results[rid] = wire
        scores[rid] = _score_from_summary(summary)
    scored = sorted(results.items(), key=lambda kv: scores[kv[0]], reverse=True)
    return dict(scored)


class CurrentHome(SerializableAsDict):
    """One current-status property in the current-homes listing."""

    rid: str
    address: str


class CurrentHomesListing(SerializableAsDict):
    """The current-homes response: the family's current house(s)."""

    homes: list[CurrentHome]


@api_router.get("/properties/current-homes", response_model=CurrentHomesListing)
async def list_current_homes():
    """The family's CURRENT house(s) — properties marked status=current —
    so a person can link their settings home fields to the right one."""
    result: list[CurrentHome] = []
    for prop in _registered_properties():
        rid = prop.rid
        status = prop.comment_status.latest_attempt()
        if not status.succeeded or (status.value_or_none() or "").strip().lower() != "current":
            continue
        att = prop.best_address.latest_attempt()
        address = str(att.value_or_none()) if att.succeeded and att.value_or_none() else ""
        result.append(CurrentHome(rid=str(rid), address=address))
    return CurrentHomesListing(homes=result)


@api_router.get("/properties/{rid}")
async def get_property(rid: str):
    prop = _require_property(rid)
    prop_json = await prop.view()
    return _attach_scrape_state(prop_json, rid)


@api_router.get("/properties/{rid}/detail")
async def get_property_detail(rid: str):
    prop = _require_property(rid)
    detail = (await prop.detail()).to_dict()
    await attach_monthly_delta(detail, rid)

    return detail


@api_router.get("/properties/{rid}/provenance")
async def get_property_provenance(rid: str):
    """The detail-surface provenance, fetched ONLY when the user reveals
    a derivation (P8) — never embedded in the detail payload. Served from
    the persisted rows; nothing recomputes."""
    prop = _require_property(rid)
    provenance = await prop.to_provenance_map()
    # The monthly delta's derivation lives on the DAG node (one tree for
    # both sides); the UI keys its toggle by side, so serve it under both.
    delta = getattr(prop, "delta_vs_home", None)
    if delta is not None:
        tree = (await delta.build_provenance()).to_dict()
        provenance["monthly_delta.couple"] = tree
        provenance["monthly_delta.others"] = tree
    return provenance




def _council_tax_payers(value) -> list[str]:
    """Validate one council-tax payer list — a 422 when it is not a list of
    person names."""
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise HTTPException(status_code=422, detail="payers must be a list of person names")
    return value




@api_router.patch("/properties/{rid}/address")
async def patch_address(rid: str, address_patch: Mapping[str, Any]):
    prop = _require_property(rid)

    prop.corrected_address.push(address_patch.get("address", ""), "user")
    # Recompute before responding — the frontend refetches the detail
    # immediately; the processor drains the cascade in order.
    return _StatusOk(status="ok")



@api_router.patch("/properties/{rid}/location")
async def patch_location(rid: str, location_patch: Mapping[str, Any]):
    prop = _require_property(rid)
    lat = location_patch.get("lat")
    lon = location_patch.get("lon")
    if lat is None or lon is None:
        raise HTTPException(status_code=422, detail="lat and lon are required")
    gp = GeoPoint(lat=lat, lon=lon)
    prop.precise_location.push(gp, "user")
    # Recompute before responding — same race as the address PATCH.
    return _StatusOk(status="ok")



@api_router.patch("/properties/{rid}/council-tax")
async def patch_council_tax(rid: str, council_tax_patch: Mapping[str, Any]):
    """Set the council-tax apportionment for a property.

    ``main_payers`` — who pays a share of the MAIN house's council tax
    (they split it equally; empty = all adults, the default headcount
    split).  ``annexe_payers`` — who pays the ANNEXE's council tax.
    ``ignored`` — the detected second dwelling is unrelated; hide it and
    exclude its costs.
    """
    prop = _require_property(rid)

    # Validate the ENTIRE council_tax_patch before pushing anything — a partial write
    # on a 422 leaves the property in a half-updated state (review).
    main_payers = (
        _council_tax_payers(council_tax_patch["main_payers"]) if "main_payers" in council_tax_patch else None
    )
    annexe_payers = (
        _council_tax_payers(council_tax_patch["annexe_payers"]) if "annexe_payers" in council_tax_patch else None
    )
    if "ignored" in council_tax_patch and not isinstance(council_tax_patch["ignored"], bool):
        raise HTTPException(status_code=422, detail="ignored must be a boolean")
    ignored = council_tax_patch.get("ignored")

    if main_payers is not None:
        prop.council_tax_payers.push(main_payers, "user")
    if annexe_payers is not None:
        prop.annexe_payers.push(annexe_payers, "user")
    if ignored is not None:
        prop.annexe_ignored.push(ignored, "user")
    return _StatusOk(status="ok")




@api_router.patch("/properties/{rid}/triage")
async def patch_triage(rid: str, triage_patch: Mapping[str, Any]):
    prop = _require_property(rid)

    def _apply_triage() -> None:
        for key, convert in (
            ("favourite", bool),
            ("dismissed", bool),
            ("is_viewed", bool),
            ("user_notes", str),
            ("triage_status", str),
        ):
            if key in triage_patch:
                getattr(prop, key).push(convert(triage_patch[key]), "user")

    _apply_triage()
    return _StatusOk(status="ok")



@api_router.patch("/properties/{rid}/rental-income")
async def patch_rental_income(rid: str, rental_income_patch: Mapping[str, Any]):
    """Update the monthly rental income for a property.

    Body: {"value": 1200} or {"value": null} to clear.
    """
    prop = _require_property(rid)

    value = rental_income_patch.get("value")
    if value is not None and not isinstance(value, (int, float)):
        raise HTTPException(
            status_code=400,
            detail="value must be a number",
        )

    await run_on_processor(
        lambda: prop.rental_income.push(
            Money(str(value), "GBP") if value is not None else Money(amount="0", currency="GBP"),
            "user",
        )
    )
    return _StatusOk(status="ok")



@api_router.patch("/properties/{rid}/works-estimate")
async def patch_works_estimate(rid: str, works_estimate_patch: Mapping[str, Any]):
    """Update the works estimate for a person on this property.

    Body: {"person": "p_ashby", "value": 15000} — the key is the
    canonical person_id (legacy names resolve); the estimate is stored
    under the id so a rename never orphans it.
    """
    prop = _require_property(rid)

    target = _validate_works_person(works_estimate_patch.get("person", ""))
    person_id = person_id_of(target)
    value = works_estimate_patch.get("value")
    _validate_works_value(value)

    def _apply() -> None:
        estimates = dict(prop.works_estimates.latest_attempt().value_or_none() or {})
        if value is None:
            estimates.pop(person_id, None)  # emptied field: drop the estimate
        else:
            estimates[person_id] = Money(str(value), "GBP")
        prop.works_estimates.push(estimates, "user")

    # Thread rule 7, no exceptions: enqueue and return. The drain runs in
    # the background; this page's update lands via the summary broadcast.
    await run_on_processor(_apply)

    return _StatusOk(status="ok")




@api_router.get("/properties/{rid}/comments")
async def get_property_comments(rid: str):
    """Return all comments for a property.

    Reads from the comments table only (the sheet integration is gone;
    its comments were migrated to this table long ago).
    """
    # Validate the property exists
    _require_property(rid)
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


def _linked_person_name(persons, *, person_id: str | None = None, email: str | None = None) -> str | None:
    """The display name of the person matching ``person_id`` or ``email``.

    Legacy name shapes resolve through ``person_key``; the person record
    stays keyed by display name. None when no person carries the identity.
    """
    for p in persons or []:
        if person_id is not None and person_key(p) != person_id:
            continue
        name = ""
        if isinstance(p, dict):
            pe = p.get("email")
            if email is not None and (pe is None or pe.casefold() != email.casefold()):
                continue
            name = p.get("name", "")
        elif hasattr(p, "email"):
            if email is not None and (p.email is None or p.email.casefold() != email.casefold()):
                continue
            name = getattr(p, "name", "")
        if name:
            return name
    return None


def _comment_person(request: Request, session_user: SessionClaims, svc) -> str:
    """Resolve the comment author — impersonation header for superusers,
    else the session email's linked person in settings.

    The header carries the canonical person_id (legacy names resolve);
    the comment record stays keyed by display name.
    """
    impersonate = request.headers.get("X-Impersonate-Person", "")
    if impersonate:
        if not session_user.is_superuser:
            raise HTTPException(status_code=403, detail="Only superusers can impersonate")
        persons_value = svc.persons_source.latest_attempt().value_or_none()
        target_id = resolve_person_identity(persons_value, impersonate)
        if target_id is None:
            raise HTTPException(status_code=400, detail=f"Unknown person {impersonate!r}")
        name = _linked_person_name(persons_value, person_id=target_id)
        if name is None:
            raise HTTPException(status_code=400, detail=f"Unknown person {impersonate!r}")
        return name
    name = _linked_person_name(svc.persons_source.latest_attempt().value_or_none(), email=session_user.email)
    if name is None:
        raise HTTPException(
            status_code=400,
            detail="Not authorised",
        )
    return name


@api_router.post("/properties/{rid}/comments")
async def add_property_comment(rid: str, body: CommentBody, request: Request):
    """Add a comment for a property.

    Person is determined from the authenticated session, with optional
    X-Impersonate-Person header for superusers.
    """
    _require_property(rid)
    session_user = effective_session_user(request)
    if not session_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    svc = get_services()
    person = _comment_person(request, session_user, svc)
    return add_comment(rid, person, body.text)


@api_router.get("/settings", response_model=SettingsPayload)
async def get_settings(request: Request):
    return await settings_payload(effective_session_user(request))


class _PersonWireFields:
    """Person-update fields by serialization kind: money-typed vs whole-pound-
    rounded (the patch serializer reads these once)."""

    MONEY_FIELDS = {"home_sale_price", "outstanding_mortgage", "cash_contribution", "life_insurance_monthly"}
    # Large house-purchase / deposit amounts are whole pounds — never pence
    # (the UI enforces this too; this is the hard guarantee).
    WHOLE_POUND_FIELDS = {"home_sale_price", "outstanding_mortgage", "cash_contribution"}


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
        except (ArithmeticError, TypeError, ValueError) as e:
            # A non-numeric amount or an unknown currency: 400 with the value.
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
    except (AttributeError, TypeError, ValueError) as e:
        # A non-numeric value or an unknown unit: 400 with the value.
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
        if not isinstance(share, int) or not _ApiPolicy.MIN_SHARE_PERCENT <= share <= _ApiPolicy.MAX_SHARE_PERCENT:
            raise ValueError(f"co-owner share must be a whole percent 1-100, got {share!r}")
        parsed.append(HomeCoOwner(name=str(item["name"]), share=share))
        total_share += share
    if total_share > _ApiPolicy.MAX_SHARE_PERCENT:
        raise ValueError(f"co-owner shares total {total_share}% — cannot exceed 100%")
    return tuple(parsed)


def _parse_home_property_rid(rid: object) -> str:
    """home_property_rid must be a string or null; null → ''."""
    if rid is not None and not isinstance(rid, str):
        raise ValueError("home_property_rid must be a string or null")
    return rid if isinstance(rid, str) else ""


def _parse_rent_paid_monthly(v: object) -> Money:
    """rent_paid_monthly is a plain monthly amount — no whole-pound rule."""
    return _parse_money(v, field="rent_paid_monthly")


type _FieldParser = Callable[[Any], Any]


class _PersonFieldParsers:
    """The single-parser person-update fields: field name → its parser.

    One table, so a field's guard and its parse cannot drift apart.  The
    money fields share _parse_money with per-field flags and keep their
    own loop; editable_by and places_of_interest have their own rules.
    """

    RULES: tuple[tuple[str, _FieldParser], ...] = (
        ("bus_walk_penalty", _parse_penalty),
        ("petrol_mpg", _parse_petrol_mpg),
        ("home_co_owners", _parse_co_owners),
        ("home_property_rid", _parse_home_property_rid),
        ("rent_paid_monthly", _parse_rent_paid_monthly),
    )


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


def _coerce_person_updates(
    updates: MutableMapping[str, Any], target: Person, merge_destinations: bool
) -> MutableMapping[str, Any]:
    """Parse each present field with its parser — the single-parser fields
    come from _PersonFieldParsers.RULES (one rule per field), the money
    fields from their own loop; extracted so _person_from_dict stays a
    merge, not a parser."""
    for f in _PersonWireFields.MONEY_FIELDS:
        if f in updates:
            updates[f] = _parse_money(updates[f], whole_pounds=f in _PersonWireFields.WHOLE_POUND_FIELDS, field=f)
    for field, parse in _PersonFieldParsers.RULES:
        if field in updates:
            updates[field] = parse(updates[field])
    if "editable_by" in updates and updates["editable_by"] is not None:
        updates["editable_by"] = tuple(updates["editable_by"])
    if "places_of_interest" in updates:
        parsed = _parse_places_of_interest(updates["places_of_interest"])
        if merge_destinations:
            by_label = {q.label: q for q in parsed}
            merged = tuple(by_label.pop(q.label, q) for q in target.places_of_interest)
            updates["places_of_interest"] = merged + tuple(by_label.values())
        else:
            updates["places_of_interest"] = parsed
    return updates


def _person_from_dict(d: Mapping[str, Any], target: Person, *, merge_destinations: bool = False) -> Person:
    """MERGE an API dict into an existing Person — never replace.

    Only the fields present in the body change; every unmentioned field
    keeps the target's value.  Replace semantics silently reset real data
    (emails, walk penalties, flags) whenever a client sends a partial
    body — that is exactly how the family emails were wiped.

    ``merge_destinations``: merge ``places_of_interest`` BY LABEL — a
    destination the body doesn't mention survives (a what-if scenario
    only names the destinations it edits, and a partial panel copy must
    never delete the rest of the household's destinations).  Without it,
    the body's list replaces the tuple (the settings UI manages the full
    destination list, where omission means removal).
    """
    updates = _coerce_person_updates({k: v for k, v in d.items() if k != "thresholds"}, target, merge_destinations)
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
async def patch_person(name: str, person_patch: Mapping[str, Any], request: Request):
    """Update one person's settings — own person / superuser / guardian.

    Replaces the whole-list PUT: the server decides who may edit whom,
    never the UI.  ``person_patch`` is the full person record (the old PUT item)
    plus an optional ``thresholds`` dict for the person's commute
    thresholds, saved to the separate thresholds source under the same
    ownership rule.
    """
    session_user = effective_session_user(request)
    if not session_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    svc = get_services()
    persons = list(svc.persons_source.latest_attempt().value_or_none() or [])
    target = next(
        (p for p in persons if person_key(p) == name or getattr(p, "name", "") == name),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail=f"No person {name!r}")

    view = SessionPersons(persons=persons, session_user=session_user)
    session_name = view.session_name()
    if not view.can_edit(session_name, target):
        raise HTTPException(
            status_code=403,
            detail="You can only edit your own settings (or a child's, if you are their guardian)",
        )

    if not session_user.is_superuser:
        # identity/privilege fields are superuser-only: editing your own
        # record must not escalate to superuser, hijack the email link,
        # rename yourself onto another person's ownership key (name IS the
        # authz identity), or flip is_child (which changes guardianship)
        person_patch = {
            **person_patch,
            "name": target.name,
            "person_id": target.person_id,
            "is_child": target.is_child,
            "is_superuser": target.is_superuser,
            "email": target.email,
            "editable_by": target.editable_by,
        }

    try:
        updated = [_person_from_dict(person_patch, target) if p is target else p for p in persons]
        svc.persons_source.push(updated, "user")
    except (ValueError, TypeError) as e:
        # malformed client input is a CLIENT error (400), never a 500
        raise HTTPException(status_code=400, detail=str(e)) from e

    thresholds = person_patch.get("thresholds")
    if isinstance(thresholds, dict):
        current = dict(svc.commute_thresholds_source.latest_attempt().value_or_none() or {})
        current[name] = thresholds
        svc.commute_thresholds_source.push(current, "user")

    return _StatusOk(status="ok")


@api_router.patch("/settings/financial")
async def patch_financial(financial_patch: Mapping[str, Any]):
    svc = get_services()

    def _apply_financial() -> None:
        for api_key, value in financial_patch.items():
            node_id = SettingsNode.API_KEY_TO_NODE.get(api_key)
            if node_id is not None and node_id in svc.setting_nodes:
                svc.setting_nodes[node_id].push(value, "user")

    _apply_financial()
    return _StatusOk(status="ok")


def _validate_works_person(person_ref: str):
    """400 guard + resolution: the person must exist; returns the
    canonical Person so the estimate is stored under its id."""
    if not person_ref:
        raise HTTPException(status_code=400, detail="person is required")
    _pa = get_services().persons_source.latest_attempt()
    _pa_value = _pa.value_or_none()
    if _pa.succeeded and _pa_value:
        pid = resolve_person_identity(_pa_value, person_ref)
        if pid is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown person: {person_ref}",
            )
        for p in _pa_value:
            if person_key(p) == pid:
                return p
    raise HTTPException(
        status_code=400,
        detail=f"Unknown person: {person_ref}",
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


class PersonSummary(SerializableAsDict):
    """One person as the /persons listing reports them."""

    name: str
    person_id: str
    email: str
    is_child: bool


class PersonsListing(SerializableAsDict):
    """The /persons response — every person, for the impersonation dropdown."""

    persons: list[PersonSummary]


@api_router.get("/persons", response_model=PersonsListing)
async def list_persons():
    """Return ALL persons (name, email when present, is_child).

    Used by the frontend superuser impersonation dropdown. The email
    filter is gone — callers that need only email-linked persons (or
    only adults) must filter themselves; the impersonate endpoint
    enforces the no-children rule server-side.
    """
    svc = get_services()
    persons_attempt = svc.persons_source.latest_attempt()
    result: list[PersonSummary] = []
    if persons_attempt.succeeded:
        result = [
            (
                PersonSummary(
                    name=p.get("name", ""),
                    person_id=p.get("person_id") or person_id_of(p),
                    email=p.get("email", ""),
                    is_child=bool(p.get("is_child")),
                )
                if isinstance(p, dict)
                else PersonSummary(
                    name=getattr(p, "name", ""),
                    person_id=person_id_of(p),
                    email=getattr(p, "email", ""),
                    is_child=bool(getattr(p, "is_child", False)),
                )
            )
            for p in persons_attempt.value_or_none() or []
        ]
    return PersonsListing(persons=result)


class SchedulerError(SerializableAsDict):
    """The non-AsyncRefreshQueue branch of /debug/scheduler."""

    type: str
    error: str


class QueueEntry(SerializableAsDict):
    """One queued node in the /debug/scheduler response."""

    node_id: str
    scheduled_at: float


class SchedulerSnapshot(SerializableAsDict):
    """The scheduler's pending work as the endpoint reports it."""

    queue_size: int
    scheduled_count: int
    wakeup_set: bool
    queue: list[QueueEntry]


@api_router.get("/debug/scheduler", response_model=SchedulerSnapshot | SchedulerError)
async def debug_scheduler():
    """Snapshot the scheduler's pending work — read-only, never drains.

    The background processor drains the queue automatically; this endpoint
    only reports what is queued right now.  (A manual get/put drain loop is
    an infinite loop — re-putting before the empty check — and blocks the
    event loop, wedging the server.)
    """
    sched = dag.scheduler.get_scheduler()
    if not isinstance(sched, AsyncRefreshQueue):
        return SchedulerError(type=type(sched).__name__, error="not AsyncRefreshQueue")

    # _scheduled: node_id -> QueueEvent, one entry per queued node (the
    # queue itself is drained by the processor — never touch it here)
    queue_snapshot = [
        QueueEntry(node_id=node_id, scheduled_at=event.scheduled_at)
        for node_id, event in list(sched._scheduled.items())[:500]
    ]

    return SchedulerSnapshot(
        queue_size=sched._queue.qsize(),
        scheduled_count=len(sched._scheduled),
        wakeup_set=sched._wakeup.is_set(),
        queue=queue_snapshot,
    )


class TypeCount(SerializableAsDict):
    """One object-type bucket in the /debug/memory response."""

    type: str
    count: int


class MemorySnapshot(SerializableAsDict):
    """Object counts by type as the endpoint reports them."""

    total_objects: int
    top_types: list[TypeCount]


@api_router.get("/debug/memory", response_model=MemorySnapshot)
async def debug_memory():
    """Count Python objects by type — helps diagnose memory leaks."""

    gc.collect()
    obj_counts = Counter(type(o).__name__ for o in gc.get_objects())
    top = obj_counts.most_common(_ApiPolicy.TOP_TYPES_LIMIT)

    return MemorySnapshot(
        total_objects=sum(obj_counts.values()),
        top_types=[TypeCount(type=t, count=c) for t, c in top],
    )
