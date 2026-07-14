from __future__ import annotations

import pytest

from dag.derived_node import flush_processor
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
        from houses.nodes.bus import BusLegAugmentNode

        transit = UserInputNode[dict]("t_bl", dict)
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
        transit.push({"augmented": True}, "test")
        walk.push(False, "test")
        route.push({}, "test")
        fare.push({}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none()["augmented"] is True
