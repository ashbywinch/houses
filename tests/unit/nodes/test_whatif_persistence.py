"""The what-if persists through the DAG — apply / state / restore.

Design (user-approved 2026-09-06, superseding the old "pure evaluation"
what-if): applying what-if values writes them through the NORMAL settings
write path so the DAG recomputes everything downstream and every surface
(cards, commute pills, deltas, detail pages) is scenario-true by
construction.

No numbers are copied anywhere. `node_results` is append-only history:
applying the scenario appends a new persons attempt, and the pre-what-if
attempt remains in the DAG's own history. The only extra state is the
`whatif_started_at` marker node — restore re-appends the persons attempt
that the marker points before, then clears the marker.
"""

from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple, TypedDict

import pytest
from fastapi.testclient import TestClient
from money import Money

from houses.geopoint import GeoPoint
from houses.model.domain import Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.server import app
from houses.services_provider import get_services
from houses.web.auth import get_serializer
from tests.unit.conftest import flush_all

_TEST_LAT = 51.5
_TEST_LON = -0.1


def _push_persons(*persons) -> None:
    """Seed the persons settings node directly (module-level cache)."""
    get_services().persons_source.push(list(persons), "user")


def _inject_session(client) -> None:
    """Add a valid signed session cookie to the test client's default cookies."""
    claims = {
        "email": "simon@example.com",
        "name": "Simon",
        "picture": "",
        "is_superuser": True,
        "impersonating": None,
    }
    client.cookies.set("session", get_serializer().dumps(claims))


class PoiPayload(TypedDict):
    label: str
    address: str
    trips_per_week: int
    weeks_per_year: int
    acceptable_modes: list[str]


class ApplyPerson(TypedDict):
    name: str
    places_of_interest: list[PoiPayload]


class PimlicoCommute(NamedTuple):
    """Simon's Pimlico commute as the DAG currently prices it."""

    trips: int
    yearly: Decimal


def _poi_payload(trips: int) -> PoiPayload:
    return PoiPayload(
        label="Pimlico",
        address="1 Pimlico Rd",
        trips_per_week=trips,
        weeks_per_year=46,
        acceptable_modes=["car"],
    )


def _apply_body(trips: int) -> ApplyPerson:
    return ApplyPerson(name="Simon", places_of_interest=[_poi_payload(trips)])


@pytest.fixture()
def whatif_world():
    """A signed-in app client with one costed property and POI-carrying
    persons, on the isolated in-memory app DB."""
    _push_persons(
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
                    address="1 Pimlico Rd",
                    trips_per_week=1,
                    weeks_per_year=46,
                    acceptable_modes=("car",),
                ),
            ),
        ),
        Person(name="Lorena", has_car=False, email="lorena@example.com"),
        Person(name="Ashby", has_car=True, cash_contribution=Money(amount="300000", currency="GBP")),
    )
    registry = get_services().property_registry
    registry.clear()
    rid = "42345678"
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

    client = TestClient(app)
    _inject_session(client)
    return client, rid


def _pimlico_commute(client, rid: str) -> PimlicoCommute:
    """Simon's Pimlico commute as the DAG currently prices it — the same
    node the commute pills render."""
    flush_all()
    detail = client.get(f"/api/properties/{rid}/detail").json()
    mcc = detail["affordability"]["monthly_commute_cost"]
    assert mcc["succeeded"], mcc.get("error")
    persons = mcc["value"]["persons"]
    entry = next(c for c in persons["Simon"]["commutes"] if c["label"] == "Pimlico")
    return PimlicoCommute(int(entry["trips_per_week"]), Decimal(entry["yearly_gbp"]))


def test_state_starts_inactive(whatif_world):
    client, _ = whatif_world
    assert client.get("/api/what-if/state").json() == {"active": False}


def test_originals_stay_in_dag_history_after_apply(whatif_world):
    """Apply APPENDS the scenario attempt; it never overwrites. The
    pre-what-if persons row remains in the DAG's node history, before the
    started-at marker — that row IS the restore reference, no copy."""
    client, rid = whatif_world
    original = _pimlico_commute(client, rid)
    assert original.trips == 1 and original.yearly > 0

    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]}).status_code == 200

    from dag.persistence import node_result_before

    svc = get_services()
    started_at = svc.whatif_started_at.latest_attempt().value_or_none()
    assert started_at, "apply must mark the what-if start"
    row = node_result_before(svc.persons_source._id, started_at)
    assert row is not None, "the pre-what-if persons attempt must still exist"
    simon = next(p for p in row["value"] if p["name"] == "Simon")
    pimlico = next(poi for poi in simon["places_of_interest"] if poi["label"] == "Pimlico")
    assert pimlico["trips_per_week"] == 1

    # And the live value is the scenario.
    scenario = _pimlico_commute(client, rid)
    assert scenario.trips == 0


def test_apply_prices_scenario_through_the_dag(whatif_world):
    """Apply writes the scenario through the NORMAL settings write: the
    DAG's own breakdown (the thing the commute pills render) shows £0
    for a 0-days scenario — no separate evaluation path, nothing
    hand-wired."""
    client, rid = whatif_world

    real = _pimlico_commute(client, rid)
    assert real.trips == 1
    assert real.yearly > 0, "test premise: the conftest drive fake must price the commute"

    resp = client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]})
    assert resp.status_code == 200, resp.text
    assert client.get("/api/what-if/state").json()["active"] is True

    scenario = _pimlico_commute(client, rid)
    assert scenario.trips == 0
    assert scenario.yearly == 0


def test_restore_reappends_original_and_marker_clears(whatif_world):
    """Restore re-appends the pre-what-if persons attempt (the one the
    started-at marker points before), however many times the scenario was
    re-applied in between; the marker clears."""
    client, rid = whatif_world
    original = _pimlico_commute(client, rid)
    assert original.trips == 1

    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]}).status_code == 200
    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(3)]}).status_code == 200
    tweaked = _pimlico_commute(client, rid)
    assert tweaked.trips == 3
    assert tweaked.yearly == Decimal("5.50") * 3 * 46

    assert client.post("/api/what-if/restore").status_code == 200
    assert client.get("/api/what-if/state").json() == {"active": False}
    restored = _pimlico_commute(client, rid)
    assert restored == original
    # A second restore is a no-op (nothing active).
    assert client.post("/api/what-if/restore").status_code == 409


def test_restore_works_from_a_fresh_process(whatif_world):
    """Marker and history live in the DAG's persistence: after apply, a
    fresh services/node read still sees an active what-if, and restore
    returns the originals."""
    client, rid = whatif_world
    original = _pimlico_commute(client, rid)

    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]}).status_code == 200

    # A restarted process reads state from persistence, not from memory.
    import houses.database as appdb

    appdb.close_db()
    fresh = get_services().whatif_started_at.latest_attempt().value_or_none()
    assert fresh, "the started-at marker must be persisted"
    assert client.get("/api/what-if/state").json()["active"] is True

    assert client.post("/api/what-if/restore").status_code == 200
    assert _pimlico_commute(client, rid) == original


def test_restore_without_active_state_is_409(whatif_world):
    client, _ = whatif_world
    assert client.post("/api/what-if/restore").status_code == 409


def test_summary_carries_commute_breakdown_for_pills(whatif_world):
    """The commute pills must show scenario-true figures: the property
    summary carries the DAG's commute breakdown, so an applied what-if
    (Pimlico 0 days) re-prices Simon's Pimlico line at £0/yr in the
    summary the cards render."""
    client, rid = whatif_world

    def summary_breakdown() -> dict:
        lst = client.get("/api/properties/all").json()
        return lst[rid]["monthly_commute_cost"]["value"]["persons"]

    flush_all()
    real = summary_breakdown()
    pimlico = next(c for c in real["Simon"]["commutes"] if c["label"] == "Pimlico")
    assert pimlico["trips_per_week"] == 1
    assert Decimal(pimlico["yearly_gbp"]) > 0

    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]}).status_code == 200
    flush_all()  # the real drain is the background processor; tests flush explicitly

    scenario = summary_breakdown()
    scen_pimlico = next(c for c in scenario["Simon"]["commutes"] if c["label"] == "Pimlico")
    assert scen_pimlico["trips_per_week"] == 0
    assert Decimal(scen_pimlico["yearly_gbp"]) == 0


def test_apply_requires_authentication(whatif_world):
    client, _ = whatif_world
    client.cookies.pop("session")
    assert client.post("/api/what-if/apply", json={"persons": []}).status_code == 401
