"""Regression: changing a destination's DAYS PER WEEK must not
recalculate the journeys themselves — no route recalculation, no new
journey rows. Only the cost multiplication (trips × daily) and the
figures downstream may update.

User report (2026-09-08): changing Pimlico to 0 days re-ran the whole
commute pipeline through the live APIs, taking minutes and wiping the
other destinations' figures along the way.
"""

from __future__ import annotations

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
    """Per journey node: its computed duration and its persisted stamp —
    any change means the journey was recalculated."""
    snapshot: dict = {}
    for key in ("Pimlico", "Bracknell", "Dad"):
        for sub in JOURNEY_SUBS:
            node = get_scheduler().registered_nodes().get(f"{rid}/Simon/{key}/{sub}")
            if node is None:
                continue
            att = node.latest_attempt()
            duration = None
            if att is not None and att.succeeded:
                value = att.value_or_none()
                if value is not None:
                    duration = str(value.duration)
            snapshot[f"{key}/{sub}"] = (duration, str(node._persisted_at))
    return snapshot


def test_trips_only_change_does_not_recalculate_journeys():
    get_services().persons_source.push(_persons(1), "user")
    rid = "42424246"
    prop = PropertyNodes(rid)
    register_property(rid, prop)
    prop._on_persons_changed()
    flush_all()

    before = _journey_snapshot(rid)
    assert before, "premise: the journeys computed for the seeded config"

    # THE EDIT: only Pimlico's days per week changes — nothing else.
    get_services().persons_source.push(_persons(3), "user")
    flush_all()

    after = _journey_snapshot(rid)

    assert after == before, f"a trips-only change must not recalculate journeys: {before} -> {after}"
