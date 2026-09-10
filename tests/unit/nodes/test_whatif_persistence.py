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
from tests.unit.conftest import drain_recompute, flush_all

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


def _pimlico_commute(client, rid: str) -> PimlicoCommute | None:
    """Simon's Pimlico commute as the DAG currently prices it — the same
    node the commute pills render. Priced at £0.00 when the scenario sets
    0 days a week (the multiplication, not a vanishing act)."""
    drain_recompute()  # make pending computation land — reads never flush persistence
    detail = client.get(f"/api/properties/{rid}/detail").json()
    mcc = detail["affordability"]["monthly_commute_cost"]
    assert mcc["succeeded"], mcc.get("error")
    persons = mcc["value"]["persons"]
    entries = [c for c in persons["Simon"]["commutes"] if c["label"] == "Pimlico"]
    if not entries:
        return None
    entry = entries[0]
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
    assert original is not None
    assert original.trips == 1 and original.yearly > 0

    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]}).status_code == 200

    flush_all()  # land the apply cascade's writes before reading history

    from dag.persistence import node_result_before

    svc = get_services()
    started_at = svc.whatif_started_at.latest_attempt().value_or_none()
    assert started_at, "apply must mark the what-if start"
    row = node_result_before(svc.persons_source._id, started_at)
    assert row is not None, "the pre-what-if persons attempt must still exist"
    simon = next(p for p in row["value"] if p["name"] == "Simon")
    pimlico = next(poi for poi in simon["places_of_interest"] if poi["label"] == "Pimlico")
    assert pimlico["trips_per_week"] == 1

    # And the live value is the scenario: priced by the plain
    # multiplication — 0 days x £5.50 x 46 = £0.00.
    after = _pimlico_commute(client, rid)
    assert after is not None
    assert after.trips == 0 and after.yearly == Decimal("0.00")


def test_apply_prices_scenario_through_the_dag(whatif_world):
    """Apply writes the scenario through the NORMAL settings write: the
    DAG's own breakdown prices a 0-days destination at £0 by the
    multiplication — no separate evaluation path, nothing hand-wired."""
    client, rid = whatif_world

    real = _pimlico_commute(client, rid)
    assert real is not None
    assert real.trips == 1
    assert real.yearly > 0, "test premise: the conftest drive fake must price the commute"

    resp = client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]})
    assert resp.status_code == 200, resp.text
    assert client.get("/api/what-if/state").json()["active"] is True

    after = _pimlico_commute(client, rid)
    assert after is not None
    assert after.trips == 0 and after.yearly == Decimal("0.00"), (
        "the scenario is priced through the DAG: 0 days contributes £0"
    )


def test_restore_reappends_original_and_marker_clears(whatif_world):
    """Restore re-appends the pre-what-if persons attempt (the one the
    started-at marker points before), however many times the scenario was
    re-applied in between; the marker clears."""
    client, rid = whatif_world
    original = _pimlico_commute(client, rid)
    assert original is not None
    assert original.trips == 1

    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]}).status_code == 200
    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(3)]}).status_code == 200
    tweaked = _pimlico_commute(client, rid)
    assert tweaked is not None
    assert tweaked.trips == 3
    assert tweaked.yearly == Decimal("5.50") * 3 * 46

    assert client.post("/api/what-if/restore").status_code == 200
    assert client.get("/api/what-if/state").json() == {"active": False}
    restored = _pimlico_commute(client, rid)
    assert restored is not None
    assert restored == original
    # A second restore is a no-op (nothing active).
    assert client.post("/api/what-if/restore").status_code == 409


def test_restore_responds_without_draining_the_cascade(whatif_world):
    """Restore must answer immediately: the re-price cascade drains in
    the background. A restore that flushed the queue inline would hang
    the click for the whole backlog (the 2026-09-08 hang report)."""
    from unittest.mock import patch

    import dag.scheduler as sched

    client, rid = whatif_world
    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]}).status_code == 200

    scheduler = sched.get_scheduler()
    with patch.object(scheduler, "process_pending", wraps=scheduler.process_pending) as spy:
        resp = client.post("/api/what-if/restore")
        assert resp.status_code == 200
        assert spy.call_count == 0, (
            "restore must not drain the DAG queue inline — the cascade belongs to the background drain"
        )
    flush_all()  # settle the cascade the restore queued


def test_detail_prices_zero_days_and_summary_drops_the_pill(whatif_world):
    """The DETAIL page must agree with the cards: an open what-if with a
    destination at zero days prices it at £0 by the multiplication, and
    the card summary carries no pill for it (not commuted)."""
    client, rid = whatif_world
    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]}).status_code == 200

    after = _pimlico_commute(client, rid)
    assert after is not None
    assert after.trips == 0 and after.yearly == Decimal("0.00"), (
        f"the detail figures must price 0 days at £0, got {after}"
    )
    # The index-card pills come from the summary's commutes dict — a
    # destination that does not happen takes no pill.
    summary = client.get("/api/properties/all").json()[rid]
    assert "Simon/Pimlico" not in summary["commutes"], (
        f"a 0-days destination must take no pill, got {sorted(summary['commutes'])}"
    )


def test_commute_total_provenance_reflects_the_scenario_trips(whatif_world):
    """The commute TOTAL and its provenance must agree with the open
    what-if: Pimlico at 0 days is priced £0 by the multiplication and
    the derivation shows 0x/wk — never 1 day a week."""
    client, rid = whatif_world
    before = _pimlico_commute(client, rid)
    assert before is not None and before.trips == 1

    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]}).status_code == 200
    # The apply returns before the drain (rule 7): the background drain
    # lands the re-price; the test environment drains explicitly.
    flush_all()

    detail = client.get(f"/api/properties/{rid}/detail").json()
    mcc = detail["affordability"]["monthly_commute_cost"]
    assert mcc["succeeded"], mcc.get("error")
    value = mcc["value"]
    simon = value["persons"].get("Simon") or {}
    labels = {c["label"]: c for c in simon.get("commutes") or []}
    assert labels["Pimlico"]["yearly_gbp"] == "0.00", (
        f"the total must price a 0-days destination at £0, got {labels.get('Pimlico')}"
    )
    assert Decimal(value["yearly_total_gbp"]) == Decimal("0.00"), (
        "Pimlico is Simon's only commuted destination: the yearly total is £0"
    )
    formula = mcc["provenance"].get("formula") or {}
    lines = [str(line.get("label", "")) for line in (formula.get("lines") or [])]
    pimlico_lines = [entry for entry in lines if "Pimlico" in entry]
    assert pimlico_lines and all("0x/wk" in entry for entry in pimlico_lines), (
        f"the provenance must show Pimlico at 0 days a week: {lines}"
    )


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


def test_apply_requires_authentication(whatif_world):
    client, _ = whatif_world
    client.cookies.pop("session")
    assert client.post("/api/what-if/apply", json={"persons": []}).status_code == 401


def _monthly_figures(client, rid: str) -> dict:
    """The monthly figures a card renders: the commute totals, the
    couple headline, and the per-person figures."""
    drain_recompute()
    detail = client.get(f"/api/properties/{rid}/detail").json()
    mcc = detail["affordability"]["monthly_commute_cost"]
    assert mcc["succeeded"], mcc.get("error")
    group = detail["affordability"]["group_monthly_cost"]
    assert group["succeeded"], group.get("error")
    persons = mcc["value"]["persons"]
    return {
        "commutes": {name: {c["label"]: c["yearly_gbp"] for c in e["commutes"]} for name, e in persons.items()},
        "commute_yearly_total": mcc["value"]["yearly_total_gbp"],
        "couple_breakdown_commutes": group["value"]["couple_breakdown"]["commutes"],
        "couple_monthly": group["value"]["couple"]["value"],
        "ashby_monthly": group["value"]["others"]["value"],
    }


def test_scenario_reprices_the_monthly_figures(whatif_world):
    """Applying a what-if must re-price the monthly figures NEARLY
    IMMEDIATELY (one drain — no refresh, no delay): dropping Pimlico to
    0 days a week must (a) re-price Pimlico to £0 in the commute
    figures, (b) DROP the monthly totals by Pimlico's contribution, (c)
    leave the untouched household members' figures alone."""
    client, rid = whatif_world

    before = _monthly_figures(client, rid)
    assert float(before["commutes"]["Simon"]["Pimlico"]) > 0, "premise: Pimlico is priced"
    assert float(before["ashby_monthly"]) > 0, "premise: Ashby's own figure is priced (cash contribution)"

    assert client.post("/api/what-if/apply", json={"persons": [_apply_body(0)]}).status_code == 200
    flush_all()

    after = _monthly_figures(client, rid)

    assert Decimal(after["commutes"]["Simon"]["Pimlico"]) == Decimal("0.00"), (
        "a 0-days destination must re-price to £0 in the figures"
    )
    assert float(after["commute_yearly_total"]) < float(before["commute_yearly_total"]), (
        f"the commute total must DROP when a commute drops to 0 days: "
        f"{before['commute_yearly_total']} -> {after['commute_yearly_total']}"
    )
    assert float(after["couple_monthly"]) < float(before["couple_monthly"]), (
        f"the couple headline must DROP when a commute drops to 0 days: "
        f"{before['couple_monthly']} -> {after['couple_monthly']}"
    )
    # The untouched member's own figure must not move.
    assert after["ashby_monthly"] == before["ashby_monthly"]

def test_group_total_tracks_breakdown_after_destination_set_change(whatif_world):
    """The destination-set rebuild path: ADDING a destination rebuilds the
    commute pipeline and its breakdown node — the GROUP TOTAL's commute
    slice must follow the NEW breakdown, never the orphaned one the cost
    node was wired to at startup. This is the live regression where the
    detail page rendered £0.00 commutes inside the monthly total while
    the breakdown itself was priced."""
    client, rid = whatif_world

    def _full_persons(pois) -> list[Person]:
        return [
            Person(
                name="Simon",
                has_car=True,
                email="simon@example.com",
                is_superuser=True,
                home_sale_price=Money(amount="550000", currency="GBP"),
                outstanding_mortgage=Money(amount="373000", currency="GBP"),
                places_of_interest=pois,
            ),
            Person(name="Lorena", has_car=False, email="lorena@example.com"),
            Person(name="Ashby", has_car=True, cash_contribution=Money(amount="300000", currency="GBP")),
        ]

    from houses.model.domain import PlaceOfInterest

    _push_persons(
        *_full_persons(
            (
                PlaceOfInterest(
                    label="Pimlico", address="1 Pimlico Rd",
                    trips_per_week=1, weeks_per_year=46, acceptable_modes=("car",),
                ),
                PlaceOfInterest(
                    label="Bracknell", address="RG12 8YA",
                    trips_per_week=1, weeks_per_year=46, acceptable_modes=("car",),
                ),
            )
        )
    )
    flush_all()

    after = _monthly_figures(client, rid)
    after = _monthly_figures(client, rid)
    assert Decimal(after["commutes"]["Simon"]["Pimlico"]) == Decimal("5.50") * 46
    assert Decimal(after["commutes"]["Simon"]["Bracknell"]) == Decimal("5.50") * 46
    # ...and the GROUP TOTAL's commute slice — the figure the detail
    # page's Commutes row renders — must equal the breakdown's monthly
    # amount, not a frozen £0 from an orphaned node.
    expected_monthly = (
        Decimal(after["commutes"]["Simon"]["Pimlico"]) + Decimal(after["commutes"]["Simon"]["Bracknell"])
    ) / Decimal(12)
    expected_monthly = expected_monthly.quantize(Decimal("0.01"))
    assert Decimal(str(after["couple_breakdown_commutes"])) == expected_monthly, (
        f"the monthly total must include the rebuilt breakdown's commutes: "
        f"got {after['couple_breakdown_commutes']}, expected {expected_monthly}"
    )
