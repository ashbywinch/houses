from __future__ import annotations

import pytest

from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint
from houses.model.domain import PlaceOfInterest


class TestTransitNode:
    @pytest.mark.asyncio
    async def test_pending_without_location(self):
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        persons = UserInputNode[list]("persons", list)

        node = TransitNode("tn", best_location=loc, poi=poi, persons_source=persons)
        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_pending_without_poi(self):
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc2", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi2", PlaceOfInterest)
        persons = UserInputNode[list]("persons2", list)

        loc.push(GeoPoint(51.5, -0.1), "test")
        node = TransitNode("tn2", best_location=loc, poi=poi, persons_source=persons)
        a = await node.attempt()
        assert a.pending


class TestWalkLegCheckNode:
    @pytest.mark.asyncio
    async def test_false_when_no_transit(self):
        from houses.nodes.transit import WalkLegCheckNode

        transit = UserInputNode[dict]("transit_w", dict)
        persons = UserInputNode[list]("persons_w", list)

        transit.push({}, "test")
        persons.push([], "test")
        node = WalkLegCheckNode("wlc", transit_node=transit, persons_source=persons)
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() is False
