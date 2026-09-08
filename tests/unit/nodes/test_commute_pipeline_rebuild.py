"""Adding or removing a destination in settings must materialize or tear
down the commute pipelines on the LIVE property — no restart.

The pipelines are graph structure, built at property construction; a
persons write that changes the destination set must trigger the
property's own rebuild (persons.changed → _on_persons_changed) so the
new commute computes and the removed one disappears from every card.
"""

from __future__ import annotations

from money import Money

from dag.scheduler import get_scheduler
from houses.model.domain import HomeCoOwner, Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.services_provider import get_services


def _poi(label: str) -> PlaceOfInterest:
    return PlaceOfInterest(
        label=label,
        address=f"{label} station",
        trips_per_week=1,
        weeks_per_year=46,
        acceptable_modes=("car",),
    )


def _persons(*simon_pois: str, simon_co_owners: tuple[HomeCoOwner, ...] = ()) -> list[Person]:
    simon = Person(
        name="Simon",
        has_car=True,
        email="simon@example.com",
        is_superuser=True,
        home_sale_price=Money(amount="550000", currency="GBP"),
        outstanding_mortgage=Money(amount="373000", currency="GBP"),
        home_co_owners=simon_co_owners,
        places_of_interest=tuple(_poi(label) for label in simon_pois),
    )
    lorena = Person(name="Lorena", has_car=False, email="lorena@example.com")
    return [simon, lorena]


def _push(persons: list[Person]) -> None:
    get_services().persons_source.push(persons, "user")


def test_added_destination_gains_a_pipeline():
    import dag.scheduler as sched_mod

    print("LEAK-DEBUG: processor_loop:", sched_mod._processor_loop, "| task:", sched_mod._processor_task)
    _push(_persons("Pimlico"))
    rid = "42424242"
    prop = PropertyNodes(rid)
    register_property(rid, prop)

    assert "Simon/Pimlico" in prop.commute_selectors
    assert "Simon/Bracknell" not in prop.commute_selectors

    _push(_persons("Pimlico", "Bracknell"))
    prop._on_persons_changed()

    assert "Simon/Bracknell" in prop.commute_selectors
    assert f"{rid}/Simon/Bracknell/commute" in get_scheduler().registered_nodes()


def test_removed_destination_loses_its_pipeline():
    _push(_persons("Pimlico", "Bracknell"))
    rid = "42424243"
    prop = PropertyNodes(rid)
    register_property(rid, prop)
    assert "Simon/Bracknell" in prop.commute_selectors

    _push(_persons("Pimlico"))
    prop._on_persons_changed()

    assert "Simon/Bracknell" not in prop.commute_selectors
    leftovers = [nid for nid in get_scheduler().registered_nodes() if nid.startswith(f"{rid}/Simon/Bracknell/")]
    assert leftovers == [], f"torn-down pipeline nodes linger: {leftovers}"
    # the surviving destination keeps its pipeline
    assert "Simon/Pimlico" in prop.commute_selectors


def test_rebuild_keeps_finances_and_co_ownership_intact():
    """The rebuild re-reads the LIVE persons value — the restored config's
    co-ownership and finances must survive a destination change (this is
    the regression that produced a person named 'Legacy')."""
    co_owners = (HomeCoOwner(name="Lorena", share=50),)
    _push(_persons("Pimlico", simon_co_owners=co_owners))
    rid = "42424244"
    prop = PropertyNodes(rid)
    register_property(rid, prop)

    # A real settings edit carries the untouched fields through (the API
    # merge preserves co-ownership the panel doesn't send).
    _push(_persons("Pimlico", "Bracknell", simon_co_owners=co_owners))
    prop._on_persons_changed()

    loaded = get_services().persons_source.latest_attempt().value_or_none()
    simon = next(p for p in loaded if p.name == "Simon")
    assert simon.home_co_owners == co_owners
    assert simon.home_sale_price == Money(amount="550000", currency="GBP")


def test_unchanged_destination_set_is_a_noop():
    """Trips/car/MPG edits must not rebuild pipelines — those nodes read
    the persons source live; only the destination set changes structure."""
    _push(_persons("Pimlico", "Bracknell"))
    rid = "42424245"
    prop = PropertyNodes(rid)
    register_property(rid, prop)
    before = {k: id(v) for k, v in prop.commute_selectors.items()}

    import dataclasses

    edited = [
        dataclasses.replace(
            p,
            places_of_interest=tuple(dataclasses.replace(q, trips_per_week=0) for q in p.places_of_interest),
        )
        for p in _persons("Pimlico", "Bracknell")
    ]
    _push(edited)
    prop._on_persons_changed()

    assert {k: id(v) for k, v in prop.commute_selectors.items()} == before, (
        "a trips-only edit must not rebuild pipeline objects"
    )
