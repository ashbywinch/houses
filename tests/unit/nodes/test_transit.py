from __future__ import annotations

import pytest

from dag.attempt import Provenance
from dag.source_node import SourceNode
from houses.geo import GeoPoint
from houses.model.domain import PlaceOfInterest


class TestTransitNode:
    @pytest.mark.asyncio
    async def test_impossible_without_location(self):
        from houses.nodes.transit import TransitNode

        loc = SourceNode[GeoPoint]("loc", GeoPoint)
        poi = SourceNode[PlaceOfInterest]("poi", PlaceOfInterest)
        persons = SourceNode[list]("persons", list)

        node = TransitNode("tn", best_location=loc, poi=poi, persons_source=persons)
        a = await node.attempt()
        assert not a.is_succeeded
        assert "best_location" in a._error

    @pytest.mark.asyncio
    async def test_impossible_without_poi(self):
        from houses.nodes.transit import TransitNode

        loc = SourceNode[GeoPoint]("loc2", GeoPoint)
        poi = SourceNode[PlaceOfInterest]("poi2", PlaceOfInterest)
        persons = SourceNode[list]("persons2", list)

        loc.push(GeoPoint(51.5, -0.1), Provenance("test"))
        node = TransitNode("tn2", best_location=loc, poi=poi, persons_source=persons)
        a = await node.attempt()
        assert not a.is_succeeded
        assert "poi" in a._error


class TestWalkLegCheckNode:
    @pytest.mark.asyncio
    async def test_false_when_no_transit(self):
        from houses.nodes.transit import WalkLegCheckNode

        transit = SourceNode[dict]("transit_w", dict)
        persons = SourceNode[list]("persons_w", list)

        node = WalkLegCheckNode("wlc", transit_node=transit, persons_source=persons)
        a = await node.attempt()
        assert a.is_succeeded
        assert a.value_or_none() is False
