from __future__ import annotations

import pytest
from money import Money

from dag.attempt import Attempt
from dag.derived_node import DerivedNode, flush_processor
from dag.user_input_node import UserInputNode
from houses.commute import CostGroup
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest


def _succeeded_walk_check(val: bool = False) -> DerivedNode:
    """Build a minimal walk-check node whose ``_attempt`` is already resolved."""
    from houses.nodes.transit import WalkLegCheckNode

    t = UserInputNode[dict]("_wc_t", dict)
    p = UserInputNode[list]("_wc_p", list)
    w = WalkLegCheckNode("_wc", transit_node=t, persons_source=p)
    w._attempt = Attempt.succeeded(val)
    return w


class TestCommuteSelectorNode:
    @pytest.mark.asyncio
    async def test_transit_takes_priority(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=walk_check,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, "config")

        transit_commute = _make_commute(duration_min=32, cost_gbp=4.50)
        bus_commute = _make_commute(duration_min=55, cost_gbp=2.00)

        transit.push(transit_commute, "TfL")
        bus.push(bus_commute, "Bus")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == transit_commute

    @pytest.mark.asyncio
    async def test_fallback_to_bus(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=walk_check,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, "config")

        bus_commute = _make_commute(duration_min=55, cost_gbp=2.00)
        bus.push(bus_commute, "Bus")

        await flush_processor()

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_impossible_when_both_fail(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=walk_check,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, "config")

        await flush_processor()

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_impossible_when_origin_missing(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=walk_check,
        )

        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, "config")

        await flush_processor()

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_recomputes_when_transit_updates(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=walk_check,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, "config")
        bus.push(_make_commute(duration_min=55, cost_gbp=2.00), "Bus")
        transit.push(_make_commute(duration_min=32, cost_gbp=4.50), "TfL")

        await flush_processor()

        assert (await node.attempt()).value_or_none().daily_cost == Money("4.50", "GBP")

        transit.push(_make_commute(duration_min=30, cost_gbp=3.00), "TfL-Updated")

        await flush_processor()
        assert (await node.attempt()).value_or_none().daily_cost == Money("3.00", "GBP")

    @pytest.mark.asyncio
    async def test_to_json_shape(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=walk_check,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        transit.push(_make_commute(duration_min=32, cost_gbp=4.50), "TfL")
        bus.push(_make_commute(duration_min=55, cost_gbp=2.00), "Bus")

        await flush_processor()

        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] is not None
        assert "error" not in j


class TestDynamicDeps:
    """Dynamic dependency tests for CommuteSelectorNode."""

    @pytest.mark.asyncio
    async def test_cost_gt_zero_rail_fare_pending_computes(self):
        """Cost > 0 means rail_fare is not an active dep, so node computes
        even when rail_fare is pending."""
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = UserInputNode[dict]("transit", dict)
        bus = UserInputNode[dict]("bus", dict)
        walk_check = _succeeded_walk_check(False)

        rail_fare = UserInputNode[dict]("rail_fare", dict)  # never pushed → pending

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=walk_check,
            rail_fare_node=rail_fare,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        # cost_gbp > 0 so rail_fare is NOT an active dep
        transit.push({"daily_cost": {"amount": 4.50, "currency": "GBP"}}, "TfL")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, "Should compute without rail_fare when cost > 0"
        assert a.value_or_none() is not None

    @pytest.mark.asyncio
    async def test_cost_zero_rail_fare_pending_blocks(self):
        """Cost = 0 means rail_fare IS an active dep, so the node blocks
        until rail_fare resolves."""
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = UserInputNode[dict]("transit", dict)
        bus = UserInputNode[dict]("bus", dict)
        walk_check = _succeeded_walk_check(False)

        rail_fare = UserInputNode[dict]("rail_fare", dict)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=walk_check,
            rail_fare_node=rail_fare,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        # cost_gbp = 0 so rail_fare IS an active dep (but still pending)
        transit.push({"daily_cost": {"amount": 0, "currency": "GBP"}}, "TfL")

        await flush_processor()

        a = await node.attempt()
        assert a.pending, "Should block when rail_fare is an active dep and pending"

        # Now resolve rail_fare
        rail_fare.push({"daily_cost": {"amount": 5.00, "currency": "GBP"}}, "NR")
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, "Should resolve once rail_fare completes"
    async def test_walk_too_long_bus_pending_blocks(self):
        """Walk check True = bus_result is active, so node blocks when bus is pending."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(True)  # walk too long → bus IS active

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=walk_check,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        transit.push(_make_commute(duration_min=32, cost_gbp=4.50), "TfL")
        # bus NOT pushed → pending

        await flush_processor()

        a = await node.attempt()
        assert a.pending, "Should block when bus is active dep and pending"

    @pytest.mark.asyncio
    async def test_walk_fine_bus_never_needed(self):
        """Walk check False = bus_result is NOT active, so node
        computes with just origin/poi/transit."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(False)  # walk fine → bus NOT active

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=walk_check,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        transit.push(_make_commute(duration_min=32, cost_gbp=4.50), "TfL")
        # bus NOT pushed — fine because it's not an active dep

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, "Should compute without bus when walk is fine"
        assert a.value_or_none() is not None


class TestWalkLegCheckNode:
    """Direct WalkLegCheckNode tests."""

    @pytest.mark.asyncio
    async def test_walk_less_than_max(self):
        from houses.nodes.transit import WalkLegCheckNode

        transit = UserInputNode[dict]("transit", dict)
        persons = UserInputNode[list]("persons", list)
        node = WalkLegCheckNode("walk_check", transit_node=transit, persons_source=persons)

        # walk_time=10m, bus_walk_penalty_minutes=30 → 10 < 30 → False
        transit.push({"walk_time": 10}, "TfL")
        persons.push([_make_person(bus_walk_penalty_minutes=30)], "persons")
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value is False

    @pytest.mark.asyncio
    async def test_walk_exceeds_max(self):
        from houses.nodes.transit import WalkLegCheckNode

        transit = UserInputNode[dict]("transit", dict)
        persons = UserInputNode[list]("persons", list)
        node = WalkLegCheckNode("walk_check", transit_node=transit, persons_source=persons)

        # walk_time=45m, bus_walk_penalty_minutes=30 → 45 > 30 → True
        transit.push({"walk_time": 45}, "TfL")
        persons.push([_make_person(bus_walk_penalty_minutes=30)], "persons")
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value is True


# ── helpers ────────────────────────────────────────────────────────────────


def _make_person(bus_walk_penalty_minutes: int = 30, name: str = "Simon"):
    """Create a minimal Person-like object with the attributes WalkLegCheckNode reads."""
    office = PlaceOfInterest("Office", "SW1V 2QQ")
    return Person(name, True, places_of_interest=(office,),
                  bus_walk_penalty_minutes=bus_walk_penalty_minutes)


def _make_commute(duration_min=32, cost_gbp=4.50):
    from pint import Quantity

    office = PlaceOfInterest("Office", "SW1V 2QQ")
    person = Person("Simon", True, places_of_interest=(office,))
    return Commute(
        person=person,
        label=office.label,
        destination=office,
        duration=Quantity(duration_min, "minute"),
        daily_cost=Money(str(cost_gbp), "GBP"),
        details=(CostGroup(legs=(), operator="TfL", cost=Money(str(cost_gbp), "GBP")),)
    )

@pytest.mark.asyncio
async def test_commute_selector_init_with_persisted_result():
    """Constructing a CommuteSelectorNode that loads a persisted result
    must not crash when _is_stale() calls _get_active_deps() before
    the subclass has set its named attributes."""
    from houses.nodes.commute import CommuteSelectorNode
    from dag.persistence import save_node_result

    NODE_ID = "test_init_crash_persisted"

    # Persist a result so the node loads from DB and _is_stale() is called
    save_node_result(NODE_ID, {
        "status": "succeeded",
        "value": {"daily_cost": {"amount": 5.0, "currency": "GBP"}},
    })

    origin = UserInputNode[GeoPoint]("origin_crash", GeoPoint)
    poi = UserInputNode[PlaceOfInterest]("poi_crash", PlaceOfInterest)
    transit = UserInputNode[dict]("transit_crash", dict)
    bus = UserInputNode[dict]("bus_crash", dict)
    walk_check = _succeeded_walk_check(False)

    node = CommuteSelectorNode(
        NODE_ID,
        origin=origin,
        poi=poi,
        transit_result=transit,
        bus_result=bus,
        walk_leg_check=walk_check,
    )
    assert node is not None
