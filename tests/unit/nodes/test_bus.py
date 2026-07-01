from __future__ import annotations

import pytest

from dag.source_node import SourceNode
from houses.geo import GeoPoint


class TestBusRouteNode:
    @pytest.mark.asyncio
    async def test_returns_dict_when_location_ok(self):
        from houses.nodes.bus import BusRouteNode

        loc = SourceNode[GeoPoint]("loc_br", GeoPoint)
        walk = SourceNode[bool]("walk_br", bool)
        transit = SourceNode[dict]("transit_br", dict)

        loc.push(GeoPoint(51.5, -0.1), "test")
        walk.push(False, "test")
        transit.push({}, "test")
        node = BusRouteNode(
            "br",
            best_location=loc,
            walk_leg_check_node=walk,
            transit_node=transit,
        )
        a = await node.attempt()
        assert a.succeeded


class TestBodsFareNode:
    @pytest.mark.asyncio
    async def test_succeeds_with_empty_route(self):
        from houses.nodes.bus import BodsFareNode

        route = SourceNode[dict]("route_bf", dict)
        node = BodsFareNode("bf", bus_route_node=route)

        route.push({}, "test")
        a = await node.attempt()
        assert a.succeeded


class TestBusLegAugmentNode:
    @pytest.mark.asyncio
    async def test_succeeds_when_walk_ok(self):
        from houses.nodes.bus import BusLegAugmentNode

        transit = SourceNode[dict]("t_bl", dict)
        walk = SourceNode[bool]("w_bl", bool)
        route = SourceNode[dict]("r_bl", dict)
        fare = SourceNode[dict]("f_bl", dict)

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
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none()["augmented"] is True
