"""Regression: saving a works estimate must update the property figures.

User report (2026-09-08): changing the cost of works on the detail page
updated nothing.

DAG thread rule 7, no exceptions: the save enqueues the mutation and
returns immediately — it never drains the queue. The re-price lands in
the background drain and reaches the page through the websocket
broadcast; a read that races the drain returns the previous snapshot
and the broadcaster corrects it.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from money import Money

from houses.geopoint import GeoPoint
from houses.model.domain import Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.server import app
from houses.services_provider import get_services as _get_services
from houses.web.auth import get_serializer
from tests.unit.conftest import flush_all

_TEST_LAT = 51.4934
_TEST_LON = -0.0098


def _prime(rid: str) -> None:
    svc = _get_services()
    svc.persons_source.push(
        [
            Person(
                name="Simon",
                has_car=True,
                email="simon@example.com",
                is_superuser=True,
                home_sale_price=Money(amount="550000", currency="GBP"),
                outstanding_mortgage=Money(amount="373000", currency="GBP"),
                places_of_interest=(
                    PlaceOfInterest(
                        label="Pimlico",
                        address="Pimlico Rd, London",
                        trips_per_week=1,
                        weeks_per_year=46,
                        acceptable_modes=("car",),
                    ),
                ),
            ),
        ],
        "user",
    )
    prop = PropertyNodes(rid)
    prop.rightmove_price.push(Money(amount="500000", currency="GBP"), "test")
    prop.rightmove_address.push("1 Test St", "test")
    prop.rightmove_bedrooms.push("3", "test")
    prop.rightmove_location.push(GeoPoint(_TEST_LAT, _TEST_LON), "test")
    prop.corrected_address.push("1 Test St, SW1V 2QQ", "test")
    prop.precise_location.push(GeoPoint(_TEST_LAT, _TEST_LON), "test")
    prop.user_entered_address.push("1 Test St, SW1V 2QQ", "test")
    prop.works_estimates.push({}, "test")
    prop.rental_income.push(Money(amount="0", currency="GBP"), "test")
    prop.comment_status.push("", "test")
    register_property(rid, prop)


def _works_total(detail: dict) -> Decimal:
    return Decimal(detail["affordability"]["total_works"]["value"]["amount"])


def _couple_monthly(detail: dict) -> Decimal:
    return Decimal(detail["affordability"]["group_monthly_cost"]["value"]["couple"]["value"])


def _client() -> TestClient:
    client = TestClient(app)
    client.cookies.set("session", get_serializer().dumps({
        "email": "simon@example.com",
        "name": "Simon",
        "picture": "",
        "is_superuser": True,
        "impersonating": None,
    }))
    return client


def test_works_save_enqueues_and_returns_without_waiting():
    """The save returns immediately: re-priced work is still pending on
    the queue afterwards, and the immediate refetch serves the previous
    snapshot (rule 5 — freshness is push-delivered)."""
    client = _client()
    _prime("42555556")
    flush_all()

    import dag.scheduler as sched

    before = client.get("/api/properties/42555556/detail").json()
    assert _works_total(before) == 0, "test premise: no works estimated yet"

    resp = client.patch(
        "/api/properties/42555556/works-estimate",
        json={"person": "Simon", "value": 12000},
    )
    assert resp.status_code == 200, resp.text

    # THE CONTRACT: the save must not drain inline. Re-priced work is
    # still queued when the request returns.
    scheduler = sched.get_scheduler()
    assert isinstance(scheduler, sched.AsyncQueueScheduler)
    queued = scheduler.enqueued_since_flush
    assert queued > 0, (
        "the save must leave the re-price queued, not drain inline"
    )

    # The drain lands; the figures are correct afterwards.
    flush_all()
    detail = client.get("/api/properties/42555556/detail").json()
    assert _works_total(detail) == Decimal("12000")
    assert _couple_monthly(detail) > _couple_monthly(before)


def test_works_figures_are_correct_after_the_drain():
    """Full sequence: save, drain once, every surface agrees."""
    client = _client()
    _prime("42555556")
    flush_all()


    resp = client.patch(
        "/api/properties/42555556/works-estimate",
        json={"person": "Simon", "value": 12000},
    )
    assert resp.status_code == 200
    flush_all()

    detail = client.get("/api/properties/42555556/detail").json()
    assert _works_total(detail) == Decimal("12000")


def test_clearing_a_works_estimate_keeps_the_detail_valid():
    """The UI sends null when the field is emptied: the estimate is
    removed, and the property detail must stay readable (serializing a
    None Money crashes the payload)."""
    client = _client()
    _prime("42555556")
    flush_all()

    assert client.patch(
        "/api/properties/42555556/works-estimate",
        json={"person": "Simon", "value": 5000},
    ).status_code == 200
    flush_all()

    resp = client.patch(
        "/api/properties/42555556/works-estimate",
        json={"person": "Simon", "value": None},
    )
    assert resp.status_code == 200, resp.text
    flush_all()

    detail = client.get("/api/properties/42555556/detail")
    assert detail.status_code == 200, "the detail must stay readable after clearing"
    assert _works_total(detail.json()) == 0
