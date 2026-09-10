"""Regression: a what-if apply must only change the fields it names.

User report (2026-09-08): with the household config Simon — Pimlico,
Bracknell, Dad — applying a what-if that mentions only Pimlico (0 days)
(a) deleted Bracknell and Dad from the household config, and
(b) overwrote Bracknell's real address with the scenario copy, re-planning
the commute to the wrong destination,
(c) left the breakdown stuck pending, so the monthly figures never
updated on the cards.

Approved semantics: the apply changes trips_per_week for the destination
it names; everything else — the other destinations, the real addresses —
survives untouched, and the figures re-price in the same drain.
"""

from __future__ import annotations

from decimal import Decimal

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


def test_apply_changes_only_the_named_trip_count():
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
    prop = PropertyNodes(rid)
    # Prime the property the way a real scrape does — without these the
    # address chain parks on unpushed user inputs and the commute
    # pipeline never prices (the dormant-chain case).
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
    # Baseline sanity — the fixture prices by the multiplication (£5.50
    # x 3 x 46 across Pimlico/Bracknell/Dad) and the formula carries the
    # 1x/wk frequency strings this test reasons about.
    base = client.get(f"/api/properties/{rid}/detail").json()
    base_mcc = base["affordability"]["monthly_commute_cost"]
    assert base_mcc["succeeded"], f"baseline breakdown stuck: {base_mcc.get('status')} {base_mcc.get('error')}"
    assert Decimal(base_mcc["value"]["persons"]["Simon"]["yearly_gbp"]) == Decimal("5.50") * 3 * 46
    base_pimlico = [fl for fl in base_mcc["provenance"]["formula"]["lines"] if "Pimlico" in fl["label"]]
    assert base_pimlico, "fixture must carry a Pimlico formula line for the stale-claim check to mean anything"
    assert all("1x/wk" in fl["label"] for fl in base_pimlico)

    # THE SCENARIO: only Pimlico named, at 0 days. The payload carries NO
    # Bracknell entry and NO Dad entry — nothing else may change.
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

    # (a) The unmentioned destinations survive with their REAL data.
    loaded = svc.persons_source.latest_attempt().value_or_none()
    assert loaded is not None
    simon = next(p for p in loaded if p.name == "Simon")
    destinations = {q.label: q.address for q in simon.places_of_interest}
    assert destinations.get("Bracknell") == REAL_BRACKNELL_ADDRESS, (
        f"the apply clobbered or deleted Bracknell: {destinations}"
    )
    assert "Dad" in destinations, f"the apply deleted Dad: {destinations}"

    # (b) The named edit landed.
    assert destinations["Pimlico"] == "Pimlico Rd, London"
    pimlico = simon.places_of_interest[0]
    assert pimlico.trips_per_week == 0

    # (c) The figures the cards render price by the plain multiplication
    # cost x trips x weeks: Pimlico's zero factor multiplies out and the
    # untouched destinations keep their real prices.
    detail = client.get(f"/api/properties/{rid}/detail").json()
    mcc = detail["affordability"]["monthly_commute_cost"]
    assert mcc["succeeded"], f"breakdown stuck: {mcc.get('status')} {mcc.get('error')}"
    assert Decimal(mcc["value"]["persons"]["Simon"]["yearly_gbp"]) == Decimal("5.50") * 2 * 46
    simon_commutes = {c["label"]: c["yearly_gbp"] for c in mcc["value"]["persons"]["Simon"]["commutes"]}
    assert "Bracknell" in simon_commutes, f"Bracknell commute vanished: {simon_commutes}"
    assert "Dad" in simon_commutes, f"Dad commute vanished: {simon_commutes}"

    # (d) The provenance the detail page shows for the couple Commutes
    # row must present the multiplication — Pimlico at 0x/wk = £0.00/yr —
    # and nowhere claim Pimlico is still commuted at its pre-scenario
    # 1x/wk (the stale tree the ⓘ rendered on 90970053).
    pimlico_lines = [fl for fl in mcc["provenance"]["formula"]["lines"] if "Pimlico" in fl["label"]]
    assert pimlico_lines, "the zero-trip destination must appear in the how-calculated lines"
    assert all("0x/wk" in fl["label"] for fl in pimlico_lines), (
        f"expected 0x/wk on the Pimlico lines: {[fl['label'] for fl in pimlico_lines]}"
    )
    assert all(fl["value"] == "£0.00/yr" for fl in pimlico_lines)

    def _claims(node: dict) -> list[str]:
        parts = [str(node.get("label") or ""), str(node.get("value") or "")]
        formula = node.get("formula")
        if formula:
            parts.append(formula.get("result") or "")
            parts.extend(f"{fl['label']} {fl['value']}" for fl in formula.get("lines") or ())
        for child in (node.get("sources") or {}).values():
            parts.extend(_claims(child))
        return [p for p in parts if p]

    stale = [s for s in _claims(mcc["provenance"]) if "Pimlico" in s and "1x/wk" in s]
    assert not stale, f"provenance still claims Pimlico at 1x/wk: {stale}"
