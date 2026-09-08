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

from fastapi.testclient import TestClient
from money import Money

from houses.model.domain import HomeCoOwner, Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.server import app
from houses.web.auth import get_serializer
from tests.unit.conftest import flush_all

REAL_BRACKNELL_ADDRESS = "Broad Lane, Bracknell"


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

    # (c) The breakdown must not stay stuck: Pimlico (0 days) is excluded,
    # Bracknell and Dad stay priced — the figures the cards render.
    detail = client.get(f"/api/properties/{rid}/detail").json()
    mcc = detail["affordability"]["monthly_commute_cost"]
    assert mcc["succeeded"], f"breakdown stuck: {mcc.get('status')} {mcc.get('error')}"
    simon_commutes = {c["label"]: c["yearly_gbp"] for c in mcc["value"]["persons"]["Simon"]["commutes"]}
    assert "Pimlico" not in simon_commutes, "0 days = not commuted"
    assert "Bracknell" in simon_commutes, f"Bracknell commute vanished: {simon_commutes}"
    assert "Dad" in simon_commutes, f"Dad commute vanished: {simon_commutes}"
