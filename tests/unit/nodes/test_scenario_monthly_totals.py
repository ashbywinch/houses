"""Regression: a what-if apply must move the monthly totals on the cards.

User report (2026-09-08, follow-up): changing Pimlico's days to zero in
the what-if panel left every card's monthly total unchanged. The
headline figures come from group_monthly_cost, which sums the commute
breakdown — Pimlico at 0 days contributes nothing, so the totals MUST
drop by Pimlico's monthly cost when the scenario applies.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from money import Money

from houses.geopoint import GeoPoint
from houses.model.domain import HomeCoOwner, Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.server import app
from houses.web.auth import get_serializer
from tests.unit.conftest import flush_all

REAL_BRACKNELL_ADDRESS = "Broad Lane, Bracknell"
_TEST_LAT, _TEST_LON = 51.4934, -0.0098


def _poi(label: str, trips: int, address: str = "Pimlico Rd, London") -> PlaceOfInterest:
    return PlaceOfInterest(
        label=label,
        address=address,
        trips_per_week=trips,
        weeks_per_year=46,
        acceptable_modes=("car",),
    )


def _simon(pois: tuple[PlaceOfInterest, ...]) -> Person:
    return Person(
        name="Simon",
        has_car=True,
        email="simon@example.com",
        is_superuser=True,
        home_sale_price=Money(amount="550000", currency="GBP"),
        outstanding_mortgage=Money(amount="373000", currency="GBP"),
        home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
        places_of_interest=pois,
    )


def _lorena() -> Person:
    return Person(
        name="Lorena",
        has_car=False,
        email="lorena@example.com",
        places_of_interest=(
            PlaceOfInterest(
                label="Aldgate",
                address="Aldgate station, London",
                trips_per_week=2,
                weeks_per_year=46,
                acceptable_modes=("walk",),
            ),
        ),
    )


def _prime_property(rid: str) -> None:
    prop = PropertyNodes(rid)
    # Prime the property the way a real scrape does — without these the
    # address chain parks on unpushed user inputs and nothing prices.
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


def _couple_monthly(detail: dict) -> float:
    gmc = detail["affordability"]["group_monthly_cost"]
    return float(gmc["value"]["couple"]["value"])


def test_scenario_drop_moves_the_monthly_totals():
    from houses.services_provider import get_services as _get_services

    svc = _get_services()
    svc.persons_source.push(
        [
            _simon(
                (
                    _poi("Pimlico", 1),
                    _poi("Bracknell", 1, REAL_BRACKNELL_ADDRESS),
                    _poi("Dad", 1),
                )
            ),
            _lorena(),
        ],
        "user",
    )
    rid = "42555556"
    _prime_property(rid)
    flush_all()

    client = TestClient(app)
    claims = {
        "email": "simon@example.com",
        "name": "Simon",
        "picture": "",
        "is_superuser": True,
        "impersonating": None,
    }
    client.cookies.set("session", get_serializer().dumps(claims))

    detail_before = client.get(f"/api/properties/{rid}/detail").json()
    monthly_before = _couple_monthly(detail_before)
    commutes_before = detail_before["affordability"]["monthly_commute_cost"]
    assert commutes_before["succeeded"], f"baseline breakdown never priced: {commutes_before.get('status')}"
    simon_before = {c["label"]: c.get("yearly_gbp") for c in commutes_before["value"]["persons"]["Simon"]["commutes"]}
    assert "Pimlico" in simon_before, f"Pimlico must be priced at 1 day: {sorted(simon_before)}"
    # The headline is monthly: Pimlico's yearly figure amortises /12.
    pimlico_monthly = float(simon_before["Pimlico"]) / 12
    assert pimlico_monthly > 0

    # THE SCENARIO: Pimlico to zero days.
    resp = client.post(
        "/api/what-if/apply",
        json={
            "persons": [
                {
                    "name": "Simon",
                    "places_of_interest": [
                        {
                            "label": "Pimlico",
                            "address": "Pimlico Rd, London",
                            "trips_per_week": 0,
                            "weeks_per_year": 46,
                            "acceptable_modes": ["car"],
                        },
                    ],
                },
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    flush_all()

    detail_after = client.get(f"/api/properties/{rid}/detail").json()
    monthly_after = _couple_monthly(detail_after)
    # THE CONTRACT: the headline total drops by Pimlico's monthly
    # contribution, to the penny (stored figures round to 2 decimals).
    expected = monthly_before - pimlico_monthly
    assert abs(monthly_after - expected) < 0.011, (
        f"monthly total did not move by Pimlico's share: "
        f"before={monthly_before} after={monthly_after} "
        f"pimlico_monthly={pimlico_monthly}"
    )
