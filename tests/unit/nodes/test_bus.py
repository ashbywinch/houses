"""Tests for bus route, fare, and leg augmentation DAG nodes."""

from __future__ import annotations

import pytest

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint


class TestBusRouteNode:
    @pytest.mark.asyncio
    async def test_returns_dict_when_location_ok(self):
        from houses.nodes.bus import BusRouteNode

        loc = UserInputNode[GeoPoint]("loc_br", GeoPoint)
        walk = UserInputNode[bool]("walk_br", bool)
        transit = UserInputNode[dict]("transit_br", dict)
        node = BusRouteNode(
            "br",
            best_location=loc,
            walk_leg_check_node=walk,
            transit_node=transit,
        )
        loc.push(GeoPoint(51.5, -0.1), "test")
        walk.push(False, "test")
        transit.push({}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded


class TestBodsFareNode:
    @pytest.mark.asyncio
    async def test_succeeds_with_empty_route(self):
        from houses.nodes.bus import BodsFareNode

        route = UserInputNode[dict]("route_bf", dict)
        node = BodsFareNode("bf", bus_route_node=route)

        route.push({}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded


class TestBusLegAugmentNode:
    @pytest.mark.asyncio
    async def test_succeeds_when_walk_ok(self):
        from money import Money
        from pint import Quantity

        from houses.model.domain import Commute, Person, PlaceOfInterest
        from houses.nodes.bus import BusLegAugmentNode

        transit = UserInputNode[Commute]("t_bl", Commute)
        walk = UserInputNode[bool]("w_bl", bool)
        route = UserInputNode[dict]("r_bl", dict)
        fare = UserInputNode[dict]("f_bl", dict)

        node = BusLegAugmentNode(
            "bla",
            transit_node=transit,
            walk_leg_check_node=walk,
            bus_route_node=route,
            bods_fare_node=fare,
        )
        commute = Commute(
            person=Person(name="", has_car=False),
            label="Test",
            destination=PlaceOfInterest(label="", postcode=""),
            duration=Quantity(30, "minute"),
            daily_cost=Money("0", "GBP"),
            mode="transit",
        )
        transit.push(commute, "test")
        walk.push(False, "test")
        route.push({}, "test")
        fare.push({}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == commute

    @pytest.mark.asyncio
    async def test_separates_bus_leg_into_own_cost_group(self):
        """Bus leg gets BODS fare when bus and tube are in separate CGs."""
        from money import Money
        from pint import Quantity

        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.model.domain import Commute, Person, PlaceOfInterest
        from houses.nodes.bus import BusLegAugmentNode

        transit = UserInputNode[Commute]("t_bl_sep", Commute)
        walk = UserInputNode[bool]("w_bl_sep", bool)
        route = UserInputNode[dict]("r_bl_sep", dict)
        fare = UserInputNode[dict]("f_bl_sep", dict)

        node = BusLegAugmentNode(
            "bla_sep",
            transit_node=transit,
            walk_leg_check_node=walk,
            bus_route_node=route,
            bods_fare_node=fare,
        )
        # Separate CGs for bus and tube (_build_cost_groups now outputs this)
        commute = Commute(
            person=Person(name="", has_car=False),
            label="Test",
            destination=PlaceOfInterest(label="", postcode=""),
            duration=Quantity(40, "minute"),
            daily_cost=Money("0", "GBP"),
            mode="transit",
            details=(
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=5),),
                    cost=None,
                ),
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.BUS, duration_minutes=15, start_station="Stop A"),),
                    operator="TfL",
                    cost=Money("13.10", "GBP"),
                ),
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.TUBE, duration_minutes=10),),
                    operator="TfL",
                    cost=None,
                ),
            ),
        )
        transit.push(commute, "test")
        walk.push(True, "test")
        route.push({"bus_stops": [{"name": "Stop A", "lat": 51.5, "lon": -0.1}]}, "test")
        fare.push({"Stop A": {"single_fare": 1.75}}, "test")
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, f"got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None

        # The bus CostGroup should have the BODS fare applied
        bus_cgs = [cg for cg in (val.details or ()) if any(leg.mode == LegMode.BUS for leg in cg.legs)]
        assert len(bus_cgs) == 1, f"Expected 1 bus CostGroup, got {len(bus_cgs)}"
        bus_cg = bus_cgs[0]
        assert bus_cg.cost is not None, "Bus CostGroup should have a fare"
        assert float(bus_cg.cost.amount) == 1.75, f"Bus CostGroup should have BODS fare £1.75, got £{bus_cg.cost}"

        # The tube CostGroup should be unchanged (still no TfL fare)
        tube_cgs = [cg for cg in (val.details or ()) if any(leg.mode == LegMode.TUBE for leg in cg.legs)]
        assert len(tube_cgs) == 1, f"Expected 1 tube CostGroup, got {len(tube_cgs)}"
        tube_cg = tube_cgs[0]
        assert tube_cg.cost is None, "Tube CostGroup should not have cost attributed yet"
