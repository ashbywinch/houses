"""Regression: a what-if apply must never delete destinations the panel
did not mention.

User report (2026-09-08): with the household config Simon — Pimlico,
Bracknell, Dad — applying a what-if that only mentions Pimlico (0 days)
deleted Bracknell and Dad from the config; their commutes vanished from
every card.

Approved semantics: an apply names the destinations it changes; the
others survive untouched.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from money import Money

from houses.geopoint import GeoPoint
from houses.model.domain import HomeCoOwner, Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import get_property, register_property
from houses.server import app
from houses.web.auth import get_serializer
from tests.unit.conftest import flush_all


def _poi(label: str, trips: int) -> PlaceOfInterest:
    return PlaceOfInterest(
        label=label,
        address=f"{label} station, London",
        trips_per_week=trips,
        weeks_per_year=46,
        acceptable_modes=("walk",),
    )


def _push_household() -> None:
    from houses.services_provider import get_services

    simon = Person(
        name="Simon",
        has_car=True,
        email="simon@example.com",
        is_superuser=True,
        home_sale_price=Money(amount="550000", currency="GBP"),
        outstanding_mortgage=Money(amount="373000", currency="GBP"),
        home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
        places_of_interest=(
            _poi("Pimlico", 1),
            _poi("Bracknell", 1),
            _poi("Dad", 1),
        ),
    )
    lorena = Person(
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
    get_services().persons_source.push([simon, lorena], "user")


def test_apply_keeps_unmentioned_destinations():
    _push_household()
    rid = "42555555"
    prop = PropertyNodes(rid)
    prop.rightmove_price.push(Money(amount="500000", currency="GBP"), "user")
    prop.rightmove_address.push("12 Test Way, Testtown", "user")
    prop.rightmove_bedrooms.push("3", "user")
    prop.rightmove_location.push(GeoPoint(51.5, -0.1), "user")
    prop.corrected_address.push("12 Test Way, Testtown", "user")
    prop.user_entered_address.push("12 Test Way, Testtown", "user")
    prop.precise_location.push(GeoPoint(51.5, -0.1), "user")
    prop.works_estimates.push({}, "user")
    prop.rental_income.push(Money(amount="0", currency="GBP"), "user")
    prop.comment_status.push("", "user")
    register_property(rid, prop)
    prop._on_persons_changed()
    flush_all()
    assert "Simon/Bracknell" in prop.commute_selectors
    assert "Simon/Dad" in prop.commute_selectors

    client = TestClient(app)
    claims = {
        "email": "simon@example.com",
        "name": "Simon",
        "picture": "",
        "is_superuser": True,
        "impersonating": None,
    }
    client.cookies.set("session", get_serializer().dumps(claims))

    # The scenario mentions only Pimlico, at 0 days — the shape a panel
    # copy sends when it holds an incomplete destinations list.
    scenario = {
        "name": "Simon",
        "places_of_interest": [
            {
                "label": "Pimlico",
                "address": "Pimlico station",
                "trips_per_week": 0,
                "weeks_per_year": 46,
                "acceptable_modes": ["car"],
            },
        ],
    }
    resp = client.post("/api/what-if/apply", json={"persons": [scenario]})
    assert resp.status_code == 200, resp.text
    flush_all()
    # The unmentioned destinations must survive in the household config.
    prop_loaded = get_property(rid)
    assert prop_loaded is not None
    prop_loaded = get_property(rid)
    assert prop_loaded is not None
    loaded = prop_loaded._svc.persons_source.latest_attempt().value_or_none()
    assert loaded is not None
    simon = next(p for p in loaded if p.name == "Simon")
    labels = [q.label for q in simon.places_of_interest]
    assert "Pimlico" in labels, "the scenario edit itself must land"
    assert "Bracknell" in labels, f"the apply deleted Bracknell: {labels}"
    assert "Dad" in labels, f"the apply deleted Dad: {labels}"
    pimlico = next(q for q in simon.places_of_interest if q.label == "Pimlico")
    assert pimlico.trips_per_week == 0

    # And on the cards: Bracknell and Dad stay priced; Pimlico (0 days)
    # is not commuted and vanishes from the figures. (The apply's flush
    # already landed everything — a second flush is a no-op the guard
    # rejects.)
    detail = client.get(f"/api/properties/{rid}/detail").json()
    mcc = detail["affordability"]["monthly_commute_cost"]
    chain = json.dumps(mcc.get("error_detail") or mcc.get("error") or "")[:400]
    print("MCC-DEBUG chain:", chain)
    assert mcc["succeeded"], mcc.get("error")
    simon_commutes = mcc["value"]["persons"]["Simon"]["commutes"]
    commute_labels = [c["label"] for c in simon_commutes]
    assert "Pimlico" not in commute_labels, "0 days = not commuted"
    assert "Bracknell" in commute_labels, f"Bracknell commute vanished: {commute_labels}"
    assert "Dad" in commute_labels, f"Dad commute vanished: {commute_labels}"
