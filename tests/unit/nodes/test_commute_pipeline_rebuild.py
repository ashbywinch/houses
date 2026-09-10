"""Adding or removing a destination in settings must materialize or tear
down the commute pipelines on the LIVE property — no restart.

The pipelines are graph structure, built at property construction; a
persons write that changes the destination set must trigger the
property's own rebuild (persons.changed → _on_persons_changed) so the
new commute computes and the removed one disappears from every card.
"""

from __future__ import annotations

from decimal import Decimal

from money import Money
from pint import Quantity

from dag.scheduler import get_scheduler
from houses.geopoint import GeoPoint
from houses.model.domain import HomeCoOwner, Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.services_provider import get_services
from tests.unit.conftest import flush_all


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


def _prime_location(prop) -> None:
    """Give the property a location so pipelines can actually price."""

    prop.rightmove_price.push(Money(amount="500000", currency="GBP"), "test")
    prop.rightmove_address.push("1 Test St", "test")
    prop.rightmove_bedrooms.push("3", "test")
    prop.rightmove_location.push(GeoPoint(51.5, -0.1), "test")
    prop.corrected_address.push("1 Test St, SW1V 2QQ", "test")
    prop.precise_location.push(GeoPoint(51.5, -0.1), "test")
    prop.user_entered_address.push("1 Test St, SW1V 2QQ", "test")
    prop.works_estimates.push({}, "test")
    prop.rental_income.push(Money(amount="0", currency="GBP"), "test")
    prop.comment_status.push("", "test")
    flush_all()


def _simon_yearly(prop) -> dict:
    val = prop.commute_breakdown.latest_attempt().value_or_none() or {}
    simon = val.get("persons", {}).get("Simon", {})
    return {c["label"]: Decimal(c["yearly_gbp"]) for c in simon.get("commutes", [])}


def test_removed_destination_group_slice_tracks_surviving_breakdown():
    """REMOVE: the group total's commute slice must equal the SURVIVING
    breakdown's monthly figure — not the orphaned breakdown it was
    wired to at construction (live 0.0-commutes regression)."""

    rid = "42424251"
    _push(_persons("Pimlico", "Bracknell"))
    prop = PropertyNodes(rid)
    _prime_location(prop)
    register_property(rid, prop)

    _push(_persons("Pimlico"))
    flush_all()

    yearly = _simon_yearly(prop)
    assert set(yearly) == {"Pimlico"}, f"only Pimlico survives: {yearly}"
    assert yearly["Pimlico"] == Decimal("5.50") * 46
    ga = prop.group_monthly_cost.latest_attempt()
    cb = (ga.value_or_none() or {}).get("couple_breakdown", {})
    expected = (yearly["Pimlico"] / Decimal(12)).quantize(Decimal("0.01"))
    assert Decimal(str(cb.get("commutes", -1))) == expected, (
        f"the group slice must track the surviving breakdown: {cb}"
    )


def test_readded_destination_prices_fresh():
    """REMOVE then RE-ADD the same label: no zombie pricing — the
    re-added pipeline prices from scratch and the group slice follows."""

    rid = "42424252"
    _push(_persons("Pimlico", "Bracknell"))
    prop = PropertyNodes(rid)
    _prime_location(prop)
    register_property(rid, prop)

    _push(_persons("Pimlico"))
    flush_all()
    _push(_persons("Pimlico", "Bracknell"))
    flush_all()

    yearly = _simon_yearly(prop)
    assert yearly == {"Pimlico": Decimal("5.50") * 46, "Bracknell": Decimal("5.50") * 46}
    ga = prop.group_monthly_cost.latest_attempt()
    cb = (ga.value_or_none() or {}).get("couple_breakdown", {})
    expected = (Decimal("5.50") * 2 * 46 / Decimal(12)).quantize(Decimal("0.01"))
    assert Decimal(str(cb.get("commutes", -1))) == expected


def test_renamed_destination_moves_the_pipeline():
    """RENAME (label change = remove + add): the old key disappears, the
    new one prices, and the group slice tracks the surviving breakdown."""

    rid = "42424253"
    _push(_persons("Pimlico"))
    prop = PropertyNodes(rid)
    _prime_location(prop)
    register_property(rid, prop)

    _push(_persons("Pimlico Office"))
    flush_all()

    assert "Simon/Pimlico" not in prop.commute_selectors
    assert "Simon/Pimlico Office" in prop.commute_selectors
    yearly = _simon_yearly(prop)
    assert yearly == {"Pimlico Office": Decimal("5.50") * 46}
    ga = prop.group_monthly_cost.latest_attempt()
    cb = (ga.value_or_none() or {}).get("couple_breakdown", {})
    expected = (yearly["Pimlico Office"] / Decimal(12)).quantize(Decimal("0.01"))
    assert Decimal(str(cb.get("commutes", -1))) == expected


def test_moved_destination_replans_route_and_respects_the_zone():
    """A destination that MOVES (same key, new address) must re-plan:
    the pipeline re-materializes with the current address, so routes
    and the congestion-zone decision follow the destination's real
    location. Never a driving commute into the charge zone — the live
    'Driving to Pimlico' regression."""

    def _simon(address: str) -> list[Person]:
        return [
            Person(
                name="Simon",
                has_car=True,
                email="simon@example.com",
                is_superuser=True,
                bus_walk_penalty=Quantity(10, "minute"),
                places_of_interest=(
                    PlaceOfInterest(
                        label="Pimlico",
                        address=address,
                        trips_per_week=1,
                        weeks_per_year=46,
                        acceptable_modes=("car",),
                    ),
                ),
            ),
            Person(name="Lorena", has_car=False, email="l@x.c"),
        ]

    rid = "42424254"
    _push(_simon("Reading RG1 1AA"))
    prop = PropertyNodes(rid)
    _prime_location(prop)
    register_property(rid, prop)

    _push(_simon("1 Drummond Gate, Pimlico, London SW1V 2QQ"))
    flush_all()

    # The zone rule holds: no driving commute is selected for a
    # congestion-charge destination (10-min walk tolerance leaves the
    # 30-min fake walk unacceptable and transit has no unit-test plan,
    # so a surviving drive would prove the address went stale).
    val = prop.commute_selectors["Simon/Pimlico"].latest_attempt().value_or_none()
    assert val is None or val.mode != "drive", (
        f"driving into the congestion zone must never be selected: {val}"
    )
