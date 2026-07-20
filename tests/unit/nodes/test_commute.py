from __future__ import annotations

import pytest
from money import Money

import dag.user_input_node  # noqa: F401 — register Money/Quantity pydantic schemas
from dag.attempt import Attempt
from dag.derived_node import DerivedNode, flush_processor
from dag.if_then_else import IfThenElseNode
from dag.node import Node
from dag.user_input_node import UserInputNode
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest


def _succeeded_walk_check(val: bool = False) -> DerivedNode:
    """Build a minimal walk-check node whose ``_attempt`` is already resolved."""
    from houses.nodes.transit import WalkLegCheckNode

    t = UserInputNode[dict]("_wc_t", dict)
    w = WalkLegCheckNode("_wc", transit_node=t)
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
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
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
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
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
    async def test_bus_selected_when_five_minutes_faster(self):
        """When bus is >=5 min faster than transit, bus is chosen."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(True)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, "config")

        # Bus is 8 min faster (>=5 threshold) → bus wins
        transit_commute = _make_commute(duration_min=40, cost_gbp=4.50)
        bus_commute = _make_commute(duration_min=32, cost_gbp=2.00)

        transit.push(transit_commute, "TfL")
        bus.push(bus_commute, "Bus")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == bus_commute

    @pytest.mark.asyncio
    async def test_transit_selected_when_bus_only_four_minutes_faster(self):
        """When bus is only 4 min faster (<5 threshold), transit is chosen."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(True)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, "config")

        # Bus is 4 min faster (<5 threshold) → transit wins
        transit_commute = _make_commute(duration_min=40, cost_gbp=4.50)
        bus_commute = _make_commute(duration_min=36, cost_gbp=2.00)

        transit.push(transit_commute, "TfL")
        bus.push(bus_commute, "Bus")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == transit_commute

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
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
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
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
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
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
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
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
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
        assert j.get("is_child") is False, "Default is_child should be False"

    @pytest.mark.asyncio
    async def test_is_child_flag_in_json(self):
        """CommuteSelectorNode with is_child=True must propagate the flag
        into both the outer wrapper AND the inner Commute value.

        A bug in ComputedTransitNode hardcodes is_child=False on the Commute
        object, so the selector must overwrite it after selection.
        """
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector_child",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
            is_child=True,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("School", "SW1V 2QQ"), "config")
        transit.push(_make_commute(duration_min=10, cost_gbp=0), "walk")
        bus.push(None, "none")

        await flush_processor()

        j = await node.to_json()
        assert j["status"] == "succeeded"
        # Outer wrapper must propagate is_child
        assert j.get("is_child") is True, (
            f"outer is_child should be True, got {j.get('is_child')}"
        )
        # Inner Commute value must also carry is_child=True so the
        # frontend's schoolWalkMin() can identify it as a school commute.
        val = j.get("value")
        assert val is not None
        assert val.get("is_child") is True, (
            f"value.is_child should be True for child commutes, "
            f"got {val.get('is_child')}. "
            f"The CommuteSelectorNode must override the transit node's "
            f"hardcoded is_child=False."
        )

    @pytest.mark.asyncio
    async def test_picks_bus_when_much_faster(self):
        """When bus is >=5 min faster than transit, bus is selected."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(True)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, "config")

        transit_commute = _make_commute(duration_min=50, cost_gbp=4.50)
        bus_commute = _make_commute(duration_min=30, cost_gbp=2.00)

        transit.push(transit_commute, "TfL")
        bus.push(bus_commute, "Bus")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == bus_commute

    @pytest.mark.asyncio
    async def test_rejects_bus_when_not_faster(self):
        """When bus is only 2 min faster (<5 threshold), transit is selected."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(True)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, "config")

        transit_commute = _make_commute(duration_min=35, cost_gbp=4.50)
        bus_commute = _make_commute(duration_min=33, cost_gbp=2.00)

        transit.push(transit_commute, "TfL")
        bus.push(bus_commute, "Bus")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == transit_commute

    @pytest.mark.asyncio
    async def test_falls_back_to_bus_when_transit_fails(self):
        """When transit is impossible and bus succeeds, bus is selected."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        # A DerivedNode with no deps that computes to impossible.
        # This way transit.succeeded is False (not pending), so
        # DerivedNode.refresh() proceeds to compute().
        class _FailingTransit(DerivedNode[dict]):
            def __init__(self):
                super().__init__("ft", dict, ())

            def compute(self):
                return Attempt.impossible("no transit route")

            async def to_json(self):
                return {"status": "impossible"}

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = _FailingTransit()
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(True)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, "config")

        bus_commute = _make_commute(duration_min=30, cost_gbp=2.00)
        bus.push(bus_commute, "Bus")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == bus_commute

    @pytest.mark.asyncio
    async def test_picks_transit_when_bus_missing(self):
        """When transit succeeds and bus is not an active dep (walk check false), transit is selected."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(False)  # bus not active

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, "config")

        transit_commute = _make_commute(duration_min=32, cost_gbp=4.50)
        transit.push(transit_commute, "TfL")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == transit_commute


class TestDynamicDeps:
    """Dynamic dependency tests for CommuteSelectorNode."""

    @pytest.mark.asyncio
    async def test_cost_gt_zero_rail_fare_pending_computes(self):
        """Cost > 0 means rail_fare is not an active dep, so node computes
        even when rail_fare is pending."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(False)

        rail_fare = commute_input_node("rail_fare")  # never pushed → pending

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_rail_fare_if(transit, rail_fare),
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        # cost_gbp > 0 so rail_fare is NOT an active dep
        transit.push(_make_commute(duration_min=32, cost_gbp=4.50), "TfL")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, "Should compute without rail_fare when cost > 0"
        assert a.value_or_none() is not None

    @pytest.mark.asyncio
    async def test_cost_zero_rail_fare_pending_blocks(self):
        """Cost = 0 means rail_fare IS an active dep, so the node blocks
        until rail_fare resolves."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")
        walk_check = _succeeded_walk_check(False)

        rail_fare = commute_input_node("rail_fare")

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_rail_fare_if(transit, rail_fare),
        )
        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        # cost_gbp = 0 so rail_fare IS an active dep (but still pending)
        transit.push(_make_commute(duration_min=32, cost_gbp=0), "TfL")

        await flush_processor()

        a = await node.attempt()
        assert a.pending, "Should block when rail_fare is an active dep and pending"

        # Now resolve rail_fare
        rail_fare.push(_make_commute(duration_min=30, cost_gbp=5.00), "NR")
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
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
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
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_noop_if(),
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        transit.push(_make_commute(duration_min=32, cost_gbp=4.50), "TfL")
        # bus NOT pushed — fine because it's not an active dep

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, "Should compute without bus when walk is fine"
        assert a.value_or_none() is not None

    @pytest.mark.asyncio
    async def test_rail_fare_applied_when_walk_check_impossible(self):
        """When walk_check is impossible (not False), bus is not active.
        RailFareNode's attempt lands in the `bus` parameter positionally,
        leaving `rail_fare=None` in compute(), so the NR fare is lost.
        This test verifies the fix — rail_fare's cost must be applied."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit2")
        bus = commute_input_node("bus2")
        rail_fare = commute_input_node("rail_fare2")

        # Walk check that is impossible (succeeded=False) — same as Lorena/Aldgate
        from dag.derived_node import DerivedNode

        class _ImpossibleWalkCheck(DerivedNode[bool]):
            def __init__(self):
                super().__init__("wci", bool, ())
                self._attempt = Attempt.impossible("no transit data")

            def compute(self):
                return self._attempt

        walk_check = _ImpossibleWalkCheck()

        node = CommuteSelectorNode(
            "rf_walk_impossible",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_rail_fare_if(transit, rail_fare),
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        # Transit has £0 cost (TfL doesn't price NR)
        transit.push(_make_commute(duration_min=32, cost_gbp=0), "TfL")
        # Rail fare computed successfully with cost
        rail_fare.push(_make_commute(duration_min=30, cost_gbp=41.0), "NR")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, "Should compute when rail_fare resolved"
        val = a.value_or_none()
        assert val is not None
        # The commute should have the rail_fare cost (£41), not the transit cost (£0)
        assert float(val.daily_cost.amount) == 41.0, f"Expected rail_fare cost £41.0, got £{val.daily_cost.amount}"

    @pytest.mark.asyncio
    async def test_rail_fare_applied_when_transit_has_unpriced_legs(self):
        """When transit has cost > 0 (e.g. from park_and_ride parking) but
        the transit legs (train/tube) have no cost attributed, the
        CommuteSelectorNode must still apply the rail_fare."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin_ul", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi_ul", PlaceOfInterest)
        transit = commute_input_node("transit_ul")
        bus = commute_input_node("bus_ul")
        rail_fare = commute_input_node("rail_fare_ul")
        walk_check = _succeeded_walk_check(False)  # bus not active

        node = CommuteSelectorNode(
            "rf_unpriced",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=_bus_if(walk_check, bus),
            rail_fare_result=_rail_fare_if(transit, rail_fare),
        )
        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        # Transit has cost > 0 (parking) but the train leg CostGroup has cost=None
        from pint import Quantity

        from houses.commute import CostGroup, LegMode

        office = PlaceOfInterest("Office", "SW1V 2QQ")
        person = Person("Simon", True, places_of_interest=(office,))
        train_leg = JourneyLeg(mode=LegMode.TRAIN, duration_minutes=30, end_station="London Waterloo")
        park_leg = JourneyLeg(mode=LegMode.PARK, duration_minutes=0)
        transit_commute = Commute(
            person=person,
            label="Office",
            destination=office,
            duration=Quantity(60, "minute"),
            daily_cost=Money("10.90", "GBP"),  # parking cost only
            mode="transit",
            details=(
                CostGroup(legs=(train_leg,), operator="", cost=None),  # unpriced transit!
                CostGroup(legs=(park_leg,), operator="Ascot Car Park", cost=Money("10.90", "GBP")),
            ),
        )
        transit.push(transit_commute, "TfL+Park")
        # Rail fare computed successfully with cost
        rail_fare.push(_make_commute(duration_min=30, cost_gbp=41.0), "NR")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, "Should compute when rail_fare resolved"
        val = a.value_or_none()
        assert val is not None
        # The commute should include both parking (10.90) and rail_fare (41.0) costs
        assert float(val.daily_cost.amount) == 51.90, f"Expected merged cost £51.90, got £{val.daily_cost.amount}"


class TestWalkLegCheckNode:
    """Direct WalkLegCheckNode tests."""

    @pytest.mark.asyncio
    async def test_walk_less_than_max(self):
        from houses.commute import CostGroup
        from houses.nodes.transit import WalkLegCheckNode

        walk_leg = JourneyLeg(mode=LegMode.WALK, duration_minutes=10)
        transit_commute = _make_commute(duration_min=32, cost_gbp=4.50)
        transit_commute = Commute(
            person=transit_commute.person,
            label=transit_commute.label,
            destination=transit_commute.destination,
            duration=transit_commute.duration,
            daily_cost=transit_commute.daily_cost,
            mode=transit_commute.mode,
            details=(CostGroup(legs=(walk_leg,), operator="", cost=None),),
        )
        transit = UserInputNode[Commute]("transit_wl", Commute)
        node = WalkLegCheckNode("walk_check_wl", transit_node=transit, max_walk=30)
        transit.push(transit_commute, "TfL")
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value is False

    @pytest.mark.asyncio
    async def test_walk_exceeds_max(self):
        from houses.commute import CostGroup
        from houses.nodes.transit import WalkLegCheckNode

        walk_leg = JourneyLeg(mode=LegMode.WALK, duration_minutes=45)
        transit_commute = _make_commute(duration_min=32, cost_gbp=4.50)
        transit_commute = Commute(
            person=transit_commute.person,
            label=transit_commute.label,
            destination=transit_commute.destination,
            duration=transit_commute.duration,
            daily_cost=transit_commute.daily_cost,
            mode=transit_commute.mode,
            details=(CostGroup(legs=(walk_leg,), operator="", cost=None),),
        )
        transit = UserInputNode[Commute]("transit_we", Commute)
        node = WalkLegCheckNode("walk_check_we", transit_node=transit, max_walk=30)
        transit.push(transit_commute, "TfL")
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value is True


# ── helpers ────────────────────────────────────────────────────────────────


def _make_person(bus_walk_penalty_minutes: int = 30, name: str = "Simon"):
    """Create a minimal Person-like object with the attributes WalkLegCheckNode reads."""
    office = PlaceOfInterest("Office", "SW1V 2QQ")
    return Person(name, True, places_of_interest=(office,), bus_walk_penalty_minutes=bus_walk_penalty_minutes)


def _make_commute(duration_min=32, cost_gbp=4.50):
    from pint import Quantity

    from houses.commute import LegMode

    office = PlaceOfInterest("Office", "SW1V 2QQ")
    person = Person("Simon", True, places_of_interest=(office,))
    leg = JourneyLeg(mode=LegMode.TRAIN, duration_minutes=duration_min, end_station="London Paddington")
    return Commute(
        person=person,
        label=office.label,
        destination=office,
        duration=Quantity(duration_min, "minute"),
        daily_cost=Money(str(cost_gbp), "GBP"),
        details=(CostGroup(legs=(leg,), operator="TfL", cost=Money(str(cost_gbp), "GBP")),),
    )


def _bus_if(walk_check: DerivedNode, bus_node: Node) -> IfThenElseNode:
    """Wrap a bus node in IfThenElseNode activated when walk check is True."""
    from houses.nodes.commute import _bus_condition

    return IfThenElseNode(
        f"_bus_if_{id(bus_node)}",
        Commute,
        condition_sources=(walk_check,),
        condition_fn=_bus_condition,
        then_branch=bus_node,
    )


def _noop_if(name: str = "noop") -> IfThenElseNode:
    """IfThenElseNode that always returns None (no branch activated)."""
    cond = UserInputNode[bool](f"_{name}_cond", bool)
    dummy = UserInputNode[str](f"_{name}_dummy", str)
    cond.push(False, "setup")
    return IfThenElseNode(
        f"_{name}",
        Commute,
        condition_sources=(cond,),
        condition_fn=lambda a: False,
        then_branch=dummy,
    )


def _rail_fare_if(transit_node: Node, rail_fare_node: Node) -> IfThenElseNode:
    """Wrap a rail_fare node in IfThenElseNode activated when NR fare is needed."""
    from houses.nodes.commute import _needs_rail_fare

    return IfThenElseNode(
        f"_rf_if_{id(rail_fare_node)}",
        Commute,
        condition_sources=(transit_node,),
        condition_fn=_needs_rail_fare,
        then_branch=rail_fare_node,
    )


@pytest.mark.asyncio
async def test_commute_selector_init_with_persisted_result():
    """Constructing a CommuteSelectorNode that loads a persisted result
    must not crash when _is_stale() calls _get_active_deps() before
    the subclass has set its named attributes."""
    from pydantic import TypeAdapter

    from dag.persistence import save_node_result
    from houses.model.domain import Commute as CommuteDomain
    from houses.nodes.commute import CommuteSelectorNode

    node_id = "test_init_crash_persisted"

    # Persist a valid Commute dict so the node can load it with TypeAdapter(Commute)
    commute = _make_commute(duration_min=5, cost_gbp=5.0)
    value_dict = TypeAdapter(CommuteDomain).dump_python(commute, mode="json")
    save_node_result(
        node_id,
        {
            "status": "succeeded",
            "value": value_dict,
        },
    )

    origin = UserInputNode[GeoPoint]("origin_crash", GeoPoint)
    poi = UserInputNode[PlaceOfInterest]("poi_crash", PlaceOfInterest)
    transit = UserInputNode[dict]("transit_crash", dict)
    bus = UserInputNode[dict]("bus_crash", dict)
    walk_check = _succeeded_walk_check(False)

    node = CommuteSelectorNode(
        node_id,
        origin=origin,
        poi=poi,
        transit_result=transit,
        bus_result=_bus_if(walk_check, bus),
        rail_fare_result=_noop_if("crash"),
    )
    node.disconnect()


class TestFareBetween:
    """RailFareRegistry.fare_between — exact pair lookup."""

    def test_fare_between_exact_match(self):
        """Direct station pair returns the fare."""
        from houses.rail_fares import RailFareRegistry
        from houses.stations import Station

        reg = RailFareRegistry()
        reg._fares_by_pair = {
            frozenset({"MAI", "PAD"}): Money(16.40, "GBP"),
        }

        maidenhead = Station("Maidenhead", "MAI", GeoPoint(51.52, -0.72))
        paddington = Station("London Paddington", "PAD", GeoPoint(51.52, -0.18))

        fare = reg.fare_between(maidenhead, paddington)
        assert fare is not None
        assert float(fare.amount) == 16.40

    def test_fare_between_no_match(self):
        """Returns None when no pair matches (no LON fallback either)."""
        from houses.rail_fares import RailFareRegistry
        from houses.stations import Station

        reg = RailFareRegistry()
        reg._fares_by_pair = {
            frozenset({"MAI", "LON"}): Money(15.00, "GBP"),
        }

        # ABW (Aberystwyth) has no LON entry, so no fallback match
        aber = Station("Aberystwyth", "ABW", GeoPoint(52.41, -4.08))
        oxford = Station("Oxford", "OXF", GeoPoint(51.75, -1.26))

        fare = reg.fare_between(aber, oxford)
        assert fare is None

    def test_fare_between_reverse_match(self):
        """Fares are symmetric — reverse pair also matches."""
        from houses.rail_fares import RailFareRegistry
        from houses.stations import Station

        reg = RailFareRegistry()
        reg._fares_by_pair = {
            frozenset({"PAD", "MAI"}): Money(16.40, "GBP"),
        }

        maidenhead = Station("Maidenhead", "MAI", GeoPoint(51.52, -0.72))
        paddington = Station("London Paddington", "PAD", GeoPoint(51.52, -0.18))

        fare = reg.fare_between(maidenhead, paddington)
        assert fare is not None
        assert float(fare.amount) == 16.40


class TestDerivedNodeProvenance:
    """DerivedNode.build_provenance uses last path segment as label."""

    @pytest.mark.asyncio
    async def test_provenance_label_uses_last_path_segment(self):
        from dag.derived_node import DerivedNode
        from dag.user_input_node import UserInputNode

        dep = UserInputNode[float]("dep", float)
        dep.push(42.0, "test")
        await flush_processor()

        class TestNode(DerivedNode[float]):
            def compute(self, val):
                return val

            def to_json(self):
                return {"status": "succeeded", "value": self._attempt.value_or_none()}

        node = TestNode("rid/person/poi/test_node", float, (dep,))
        await flush_processor()

        prov = await node.build_provenance()
        assert prov.label == "Test Node", f"Expected 'Test Node', got '{prov.label}'"
        assert "dep" in prov.sources


class TestRailFareNode:
    """RailFareNode — fare enrichment, pass-through, and missing-dependency behavior."""

    @pytest.mark.asyncio
    async def test_pending_when_no_transit(self):
        """Node stays pending when transit has no result yet."""
        from houses.nodes.commute import RailFareNode

        transit = UserInputNode[Commute]("rf_pend", Commute)
        location = UserInputNode[GeoPoint]("rf_pend_loc", GeoPoint)
        # transit NOT pushed — node should remain pending

        node = RailFareNode("rf_pend_test", transit_result=transit, best_location=location)
        await flush_processor()

        a = await node.attempt()
        assert a.pending, f"Expected pending, got {a.status}"

    @pytest.mark.asyncio
    async def test_impossible_when_location_missing(self):
        """Node returns impossible when location dep isn't activated (cost > 0)."""
        from houses.nodes.commute import RailFareNode

        transit = UserInputNode[Commute]("rf_skip", Commute)
        location = UserInputNode[GeoPoint]("rf_skip_loc", GeoPoint)

        transit.push(_make_commute(cost_gbp=5.0), "TfL")

        node = RailFareNode("rf_skip_test", transit_result=transit, best_location=location)
        # Transit cost is non-zero, so location won't be activated
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded  # passes through transit_attempt
        val = a.value_or_none()
        assert val is not None
        assert val.daily_cost.amount == 5.0  # unchanged from transit

    @pytest.mark.asyncio
    async def test_enriches_commute_with_rail_fare(self, tmp_path):
        """Commute with zero daily cost and train leg gets NR fare added (17.00 + 2.80) × 2 → 39.60."""
        from unittest.mock import patch

        from pint import Quantity

        from houses.commute import LegMode
        from houses.nodes.commute import RailFareNode
        from houses.services_provider import get_services
        from houses.rail_fares import RailFareRegistry
        from houses.stations import StationRegistry

        # Set up registry with stations and fare
        stations_csv = tmp_path / "stations.csv"
        stations_csv.write_text(
            "stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\nFenchurch Street,FST,51.511,-0.079\n"
        )
        fares_csv = tmp_path / "fares.csv"
        fares_csv.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,FST,17.00\n")

        reg = RailFareRegistry(
            station_registry=StationRegistry(_stations_csv=stations_csv),
            _fares_csv=fares_csv,
        )
        get_services().rail_fare_registry = reg

        transit = UserInputNode[Commute]("rf_fare", Commute)
        location = UserInputNode[GeoPoint]("rf_fare_loc", GeoPoint)

        office = PlaceOfInterest("Office", "EC3A 7LP")
        person = Person("Lorena", True, places_of_interest=(office,))
        commute = Commute(
            person=person,
            label=office.label,
            destination=office,
            duration=Quantity(78, "minute"),
            daily_cost=Money("0", "GBP"),
            details=(
                CostGroup(
                    legs=(
                        JourneyLeg(
                            mode=LegMode.BUS, duration_minutes=10, start_station="", end_station="", line_name=""
                        ),
                        JourneyLeg(
                            mode=LegMode.TRAIN,
                            duration_minutes=30,
                            start_station="WOK",
                            end_station="Fenchurch Street",
                            line_name="Great Western Railway",
                        ),
                    ),
                    operator="TfL",
                    cost=Money("0", "GBP"),
                ),
            ),
        )

        transit.push(commute, "TfL")
        location.push(GeoPoint(51.317, -0.556), "geocode")

        node = RailFareNode("rf_fare_test", transit_result=transit, best_location=location)

        with patch("houses.transit_route.get_tube_leg_fare", return_value=None):
            await flush_processor()

        a = await node.attempt()
        assert a.succeeded, f"Expected succeeded, got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        # (17.00 + 2.80) × 2 = 39.60
        assert float(val.daily_cost.amount) == 39.60
        # The transit CostGroup must also have its cost attributed
        transit_cg = next((cg for cg in val.details if cg.operator == "TfL"), None)
        assert transit_cg is not None, "TfL CostGroup should exist"
        assert transit_cg.cost is not None, "TfL CostGroup should have cost attributed"
        assert float(transit_cg.cost.amount) == 39.60, f"Expected TfL CostGroup cost £39.60, got {transit_cg.cost}"


@pytest.mark.asyncio
async def test_commute_selector_impossible_without_bus():
    """When transit fails and bus_result is not an active dep
    (default None in compute), _impossible() must not crash."""
    from houses.nodes.commute import CommuteSelectorNode

    origin = UserInputNode[GeoPoint]("origin_nb", GeoPoint)
    poi = UserInputNode[PlaceOfInterest]("poi_nb", PlaceOfInterest)
    transit = UserInputNode[dict]("transit_nb", dict)
    walk_check = _succeeded_walk_check(False)

    # Provide a bus node so the constructor doesn't get None as dep,
    # but walk_check returns False so bus is NOT an active dep.
    bus = UserInputNode[dict]("bus_nb", dict)
    # Don't push bus — it'll be pending, but not added to active deps

    node = CommuteSelectorNode(
        "commute_nb",
        origin=origin,
        poi=poi,
        transit_result=transit,
        bus_result=_bus_if(walk_check, bus),
        rail_fare_result=_noop_if("nb"),
    )

    origin.push(GeoPoint(51.5, -0.1), "user")
    poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
    # Don't push transit — it'll be pending, so the selector can't
    # pick any route.  Crash was in _impossible() receiving None
    # for non-active deps.
    await flush_processor()

    a = await node.attempt()
    assert a.pending, f"Expected pending, got {a.status}: {a.error}"
