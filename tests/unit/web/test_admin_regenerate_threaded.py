"""Regression (live 2026-09-09): /api/admin/regenerate must execute
force_regenerate ON the processor thread.

The fail-fast check (assert_mutation_allowed) rejects off-thread DAG
mutation — an endpoint that calls node.refresh directly can only ever
answer 500 on the real threaded server. Unit tests never caught it
because they run the scheduler inline (testing mode), where the check
passes vacuously. This test starts the REAL processor thread.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from money import Money

import dag.persistence as persistence_mod
import dag.scheduler as sched_mod
from dag.scheduler import flush_processor, run_on_processor
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


def _build_world() -> None:
    """All DAG mutation happens ON the processor thread (thread rules)."""
    svc = get_services()
    # The TestClient request path is the app: app_mode=True satisfies
    # the settings-write guard for the direct seeding push.
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


@pytest.mark.asyncio
async def test_admin_regenerate_runs_on_the_processor_thread():
    prev_testing = persistence_mod.testing
    persistence_mod.testing = False
    try:
        sched_mod.start_processor()
        await run_on_processor(_build_world)
        # The queue lives on the processor loop now — drain THROUGH it.
        await run_on_processor(flush_processor)

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
    finally:
        sched_mod.stop_processor()
        persistence_mod.testing = prev_testing
