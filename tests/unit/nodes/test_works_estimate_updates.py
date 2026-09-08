"""Regression: saving a works estimate must update the property figures.

User report (2026-09-08): changing the cost of works on the detail page
updated nothing. The page saves with PATCH and immediately refetches
the detail — the endpoint returned BEFORE the background drain had
recomputed, so the refetch served the old figures.

Contract: an edit endpoint that the UI refetches after (works estimate,
like address/location/council-tax) must leave the figures ready when it
returns. The re-price for works is pure arithmetic, so the inline drain
is fast; the websocket summary broadcast still happens on top for the
other devices.
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


def _couple_monthly(detail: dict) -> Decimal:
    return Decimal(detail["affordability"]["group_monthly_cost"]["value"]["couple"]["value"])


def test_works_estimate_refetch_shows_the_new_figures_immediately():
    client = TestClient(app)
    _prime("42555556")
    flush_all()

    cookie = client.cookies
    cookie.set("session", get_serializer().dumps({
        "email": "simon@example.com",
        "name": "Simon",
        "picture": "",
        "is_superuser": True,
        "impersonating": None,
    }))

    detail_before = client.get("/api/properties/42555556/detail").json()
    monthly_before = _couple_monthly(detail_before)
    works_before = detail_before["affordability"]["total_works"]["value"]["amount"]
    assert Decimal(works_before) == 0, "test premise: no works estimated yet"

    # THE USER'S FLOW: save a works estimate, then the page refetches
    # the detail immediately (CostsSection.saveEdit → loadDetail(force)).
    resp = client.patch(
        "/api/properties/42555556/works-estimate",
        json={"person": "Simon", "value": 12000},
    )
    assert resp.status_code == 200, resp.text

    detail_after = client.get("/api/properties/42555556/detail").json()
    works_after = Decimal(
        detail_after["affordability"]["total_works"]["value"]["amount"]
    )
    monthly_after = _couple_monthly(detail_after)

    # THE CONTRACT: the refetch the page performs right after saving
    # must already see the re-priced figures — the works total is the
    # new value and the headline has moved (the works' exact share of
    # the headline is the monthly-cost apportionment's business, covered
    # by test_monthly_costs).
    assert works_after == Decimal("12000"), (
        f"the refetch right after saving must show the new works total, "
        f"got {works_after}"
    )
    assert monthly_after > monthly_before, (
        f"the headline must move immediately after saving works: "
        f"before={monthly_before} after={monthly_after}"
    )
