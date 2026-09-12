"""Regression: changing a destination's DAYS PER WEEK must not make
more API calls — no route recalculation. The journeys re-stamp their
cached legs with the live POI (fresh rows, current provenance) and only
the cost multiplication (trips × daily) and the figures downstream
update.

User report (2026-09-08): changing Pimlico to 0 days re-ran the whole
commute pipeline through the live APIs, taking minutes and wiping the
other destinations' figures along the way.
"""

from __future__ import annotations

import pytest
from money import Money

from dag.scheduler import get_scheduler
from houses.model.domain import HomeCoOwner, Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.services_provider import get_services
from tests.unit.conftest import flush_all


def _poi(label: str, trips: int) -> PlaceOfInterest:
    return PlaceOfInterest(
        label=label,
        address=f"{label} Rd, London",
        trips_per_week=trips,
        weeks_per_year=46,
        acceptable_modes=("car",),
    )


def _persons(pimlico_trips: int) -> list[Person]:
    simon = Person(
        name="Simon",
        has_car=True,
        email="simon@example.com",
        is_superuser=True,
        home_sale_price=Money(amount="550000", currency="GBP"),
        outstanding_mortgage=Money(amount="373000", currency="GBP"),
        home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
        places_of_interest=(
            _poi("Pimlico", pimlico_trips),
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
    return [simon, lorena]


JOURNEY_SUBS = ("walk", "tfl_no_bus", "tfl_with_bus", "drive", "commute", "merge", "final_fuel")


def _journey_snapshot(rid: str) -> dict:
    """Per journey node: its computed duration (the journey) and its
    destination stamp (the POI trips the provenance shows)."""
    snapshot: dict = {}
    for key in ("Pimlico", "Bracknell", "Dad"):
        for sub in JOURNEY_SUBS:
            node = get_scheduler().registered_nodes().get(f"{rid}/Simon/{key}/{sub}")
            if node is None:
                continue
            att = node.latest_attempt()
            duration = None
            trips = None
            if att is not None and att.succeeded:
                value = att.value_or_none()
                if value is not None:
                    duration = str(value.duration)
                    dest = getattr(value, "destination", None)
                    trips = getattr(dest, "trips_per_week", None)
            snapshot[f"{key}/{sub}"] = (duration, trips)
    return snapshot


@pytest.fixture()
def replan_tripwire():
    """Tripwires on THIS test's four planner computes: a full recompute
    (the re-plan path through the live APIs) trips them; a re-stamp
    (cached legs, live POI stamp) does not. Per-node, so cross-test
    registry leakage cannot pollute the count — the global planner is
    shared process-wide and other tests' chains re-plan on every push.
    """
    trips: dict[str, int] = {}

    def _arm(node, name: str) -> None:
        real_compute = node.compute

        async def _counting(*a, **k):
            trips[name] = trips.get(name, 0) + 1
            return await real_compute(*a, **k)

        node.compute = _counting.__get__(node, type(node))

    return trips, _arm


def test_trips_only_change_makes_no_api_calls(replan_tripwire):
    from houses.geopoint import GeoPoint

    trips, arm = replan_tripwire
    get_services().persons_source.push(_persons(1), "user")
    rid = "42424246"
    prop = PropertyNodes(rid)
    # Prime the property the way a real scrape does — without the address
    # chain the commute pipeline never prices (the dormant-chain case).
    prop.rightmove_price.push(Money(amount="500000", currency="GBP"), "test")
    prop.rightmove_address.push("1 Test St", "test")
    prop.rightmove_bedrooms.push("3", "test")
    prop.rightmove_location.push(GeoPoint(51.4934, -0.0098), "test")
    prop.corrected_address.push("1 Test St, SW1V 2QQ", "test")
    prop.precise_location.push(GeoPoint(51.4934, -0.0098), "test")
    prop.user_entered_address.push("1 Test St, SW1V 2QQ", "test")
    prop.works_estimates.push({}, "test")
    prop.rental_income.push(Money(amount="0", currency="GBP"), "test")
    prop.comment_status.push("", "test")
    register_property(rid, prop)
    prop._on_persons_changed()
    flush_all()

    before = _journey_snapshot(rid)
    assert before, "premise: the journeys computed for the seeded config"
    # The fake plans walk + drive; TfL is impossible in unit tests (the
    # _NoPlanTflClient) — those legs stay unpriced by construction, and
    # the re-stamp gate skips them (nothing to re-stamp). The premise
    # covers the priced legs.
    priced = {k: v for k, v in before.items() if v[0] is not None}
    assert priced, f"premise: some journeys priced, got {before}"

    # Arm the tripwires AFTER the seed drain: only the trips-edit drain
    # may trip them. Walk + drive plan through the fake (re-plan is a
    # real API-shaped call); the TfL legs are impossible in unit tests
    # (the _NoPlanTflClient returns impossible without planning — there
    # is no plan call to count), so the tripwires cover the two legs
    # that CAN re-plan.
    sched = get_scheduler()
    for sub in ("walk", "drive"):
        node = sched.registered_nodes().get(f"{rid}/Simon/Pimlico/{sub}")
        assert node is not None, f"premise: Pimlico/{sub} pipeline exists"
        arm(node, sub)

    # THE EDIT: only Pimlico's days per week changes — nothing else.
    get_services().persons_source.push(_persons(3), "user")
    flush_all()

    after = _journey_snapshot(rid)

    # The journeys are NOT re-planned: durations identical everywhere.
    assert {k: v[0] for k, v in after.items()} == {k: v[0] for k, v in before.items()}, (
        f"a trips-only change must not re-plan journeys: {before} -> {after}"
    )
    # But the stamps follow the live POI on the priced legs: Pimlico's
    # chain re-stamped to 3 trips (fresh rows + current provenance);
    # the others untouched.
    for k in priced:
        if k.startswith("Pimlico/"):
            assert after[k][1] == 3, f"{k} stamp did not follow the live POI: {after}"
        else:
            assert after[k] == before[k], f"untouched {k} changed: {before} -> {after}"
    # And no planner compute ran during the drain — the re-stamp path
    # touches cached legs only.
    assert trips == {}, f"a trips-only change re-ran planner computes: {trips}"
