"""The /api/admin/regenerate endpoint executes force_regenerate through
the processor seam and answers with the regeneration report.

Regression context (live 2026-09-09): an endpoint that mutated DAG nodes
DIRECTLY could only ever answer 500 in production — assert_mutation_allowed
rejects off-thread mutation, and the endpoint is an ordinary request
handler. The fix routes the work through run_on_processor.

Threading is NOT tested here — it is correct by construction (thread
rules, docs/dag-library.md): the processor loop owns all mutation, the
guard enforces it in production, and run_on_processor is the single
handover. What is pinned, deterministically: the endpoint answers 200,
matches the requested patterns, and reports every regenerated node as
succeeded. If the endpoint ever drops the run_on_processor seam, the
guard turns the production path into a loud 500 — the construction's
defense, not a test's.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from money import Money

from dag.scheduler import flush_processor
from houses.geopoint import GeoPoint
from houses.model.domain import Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.server import app
from houses.services_provider import get_services
from houses.web.auth import get_serializer

_RID = "42424250"


def _persons() -> list[Person]:
    return [
        Person(
            name="Simon",
            has_car=True,
            email="s@x.c",
            is_superuser=True,
            places_of_interest=(
                PlaceOfInterest(
                    label="Pimlico",
                    address="1 Pimlico Rd",
                    trips_per_week=1,
                    weeks_per_year=46,
                    acceptable_modes=("car",),
                ),
            ),
        ),
    ]


def _inject_session(client) -> None:
    claims = {
        "email": "s@x.c",
        "name": "Simon",
        "picture": "",
        "is_superuser": True,
        "impersonating": None,
    }
    client.cookies.set("session", get_serializer().dumps(claims))


@pytest.mark.asyncio
async def test_admin_regenerate_reports_the_regeneration():
    svc = get_services()
    svc.persons_source.push(_persons(), "user", app_mode=True)
    prop = PropertyNodes(_RID)
    prop.rightmove_price.push(Money(amount="500000", currency="GBP"), "user")
    prop.rightmove_address.push("1 Test St", "user")
    prop.rightmove_bedrooms.push("3", "user")
    prop.rightmove_location.push(GeoPoint(51.5, -0.1), "user")
    prop.corrected_address.push("1 Test St, SW1V 2QQ", "user")
    prop.precise_location.push(GeoPoint(51.5, -0.1), "user")
    prop.user_entered_address.push("1 Test St, SW1V 2QQ", "user")
    prop.works_estimates.push({}, "user")
    prop.rental_income.push(Money(amount="0", currency="GBP"), "user")
    prop.comment_status.push("", "user")
    register_property(_RID, prop)
    await flush_processor()

    client = TestClient(app)
    _inject_session(client)
    resp = client.post(
        "/api/admin/regenerate",
        json={"patterns": [f"{_RID}/commute_breakdown", f"{_RID}/group_monthly_cost"]},
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["matched"] == 2, report
    assert all(entry["status"] == "succeeded" for entry in report["regenerated"]), report
