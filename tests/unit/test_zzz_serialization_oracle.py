"""TEMPORARY serialization oracle — captures pre-migration bytes.

Delete after the Pydantic migration is verified. Writes the exact JSON the
app emits today to /tmp/ll-goldens/ so every migration step can be diffed
against it.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from money import Money

from dag.scheduler import flush_processor
from houses.geopoint import GeoPoint

OUT = pathlib.Path("/tmp/ll-goldens")
OUT.mkdir(exist_ok=True)


@pytest.fixture(autouse=True)
def _fake_services():
    from money import Money
    from pint import Quantity

    from dag.attempt import Attempt
    from houses.model.domain import Commute, Person, PlaceOfInterest
    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services

    class _FakePlanner:
        async def walk_route(self, origin, destination, max_walk):
            return Attempt.succeeded(
                Commute(
                    person=Person(name="Simon", has_car=False),
                    label="Walk",
                    destination=PlaceOfInterest(label="Dest", address=destination),
                    duration=Quantity(30, "minute"),
                    daily_cost=Money("0", "GBP"),
                ),
            )

        async def drive_route(self, origin, destination):
            return Attempt.succeeded(
                Commute(
                    person=Person(name="Simon", has_car=True),
                    label="Drive",
                    destination=PlaceOfInterest(label="Dest", address=destination),
                    duration=Quantity(20, "minute"),
                    daily_cost=Money("5.50", "GBP"),
                ),
            )

    class _FakeTflClient:
        def __init__(self, *args, **kwargs):
            self._plan_override = None
            self._no_route_detail = ""

        async def plan(self):
            return Attempt.succeeded(
                Commute(
                    person=Person(name="Simon", has_car=False),
                    label="Office",
                    destination=PlaceOfInterest(label="Office", address="SW1P 1AA"),
                    duration=Quantity(32, "minute"),
                    daily_cost=Money("4.50", "GBP"),
                ),
            )

    token = _sp.set(make_services(route_planner=_FakePlanner(), tfl_client_factory=_FakeTflClient))
    yield
    _sp.reset(token)


@pytest.fixture
def prop():
    from houses.nodes.property_nodes import PropertyNodes

    p = PropertyNodes("test_shape")
    p.rightmove_price.push(Money("550000", "GBP"), "test")
    p.rightmove_address.push("31 Isambard Rd", "test")
    p.rightmove_bedrooms.push("3", "test")
    p.rightmove_location.push(GeoPoint(51.48, -0.35), "rightmove")
    p.corrected_address.push("31 Isambard Rd, SW1P 1AA", "test")
    p.precise_location.push(GeoPoint(51.5, -0.37), "test")
    p.user_entered_address.push("31 Isambard Rd, SW1P 1AA", "test")
    p.works_estimates.push({"3": Money("0", "GBP")}, "test")
    p.comment_status.push("", "test")
    return p


def _write(name: str, payload) -> None:
    (OUT / name).write_text(json.dumps(payload, indent=2, default=str, sort_keys=False) + "\n")


@pytest.mark.asyncio
async def test_capture_node_projections(prop):
    _write("node_json.json", (await prop.view()).to_dict())
    _write("node_summary.json", (await prop.summary()).to_dict())
    _write("node_detail.json", (await prop.detail()).to_dict())


@pytest.mark.asyncio
async def test_capture_dag_rows(prop):
    await flush_processor()
    from dag import persistence

    conn = persistence._get_db()
    rows = list(
        conn.execute(
            "SELECT node_id, status, value_json, provenance_z FROM node_results "
            "WHERE node_id LIKE 'test_shape%' ORDER BY node_id"
        )
    )
    _write("dag_rows.json", [dict(r) for r in rows])


def test_capture_api_endpoints(prop):
    from houses.property_registry import register_property
    from houses.server import app
    from houses.web.auth import _make_session_cookie

    register_property("89306649", prop)
    client = TestClient(app)
    client.cookies.set(
        "session",
        _make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=True),
    )
    paths = [
        "/api/settings",
        "/api/persons",
        "/api/debug/scheduler",
        "/api/debug/memory",
        "/api/properties/all",
        "/api/properties/89306649",
        "/api/properties/89306649/detail",
        "/api/properties/89306649/staleness",
        "/api/properties/89306649/provenance",
        "/api/properties/current-homes",
    ]
    captured = {}
    for path in paths:
        resp = client.get(path)
        captured[path] = {"status": resp.status_code, "body": resp.text}
    _write("api.json", captured)


def test_capture_envelopes(prop):
    """The envelope bytes must equal the pre-migration json.dumps expressions."""
    from houses.web.broadcaster import _PropertyUpdatedEnvelope, _SettingsUpdatedEnvelope

    summary = {"rid": "89306649", "value": "x"}
    msg = _PropertyUpdatedEnvelope(rid="89306649", data=summary).to_json()
    assert msg == json.dumps({"type": "property_updated", "rid": "89306649", "data": summary})
    _write("envelope_property.json", json.loads(msg))

    payload = _minimal_settings()
    settings_msg = _SettingsUpdatedEnvelope(data=payload).to_json()
    assert settings_msg == json.dumps(
        {"type": "settings_updated", "data": payload.model_dump(by_alias=True)},
    )
    _write("envelope_settings.json", json.loads(settings_msg))


def _minimal_settings():
    """A settings document with every field kind populated once."""
    from houses.web.settings_payload import (
        DepositFormula,
        DepositProvenance,
        FinancialBlock,
        HouseholdDeposit,
        ProvenanceLine,
        SettingsPayload,
    )

    return SettingsPayload(
        persons={"status": "succeeded", "value": []},
        financial=FinancialBlock(status="succeeded", value={"mortgage_rate": 0.0495}),
        commute_thresholds={"value": {"max_commute": 60}},
        household_deposit=HouseholdDeposit(
            total=Money("477000", "GBP"),
            persons={"Simon": Money("177000", "GBP")},
            provenance=DepositProvenance(
                label="Household Deposit",
                value="£477,000.00",
                source_type="calc",
                formula=DepositFormula(lines=[ProvenanceLine(label="Simon", value="50% yours")], result="£477,000.00"),
            ),
        ),
        what_if_active=False,
    )
