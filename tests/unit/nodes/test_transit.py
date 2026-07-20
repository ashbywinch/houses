from __future__ import annotations

import pytest
from money import Money
from pint import Quantity

from dag.derived_node import flush_processor
from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest


class TestTransitNode:
    @pytest.mark.asyncio
    async def test_pending_without_location(self):
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)

        node = TransitNode("tn", best_location=loc, poi=poi, has_car=False, max_walk=30)
        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_pending_without_poi(self):
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc2", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi2", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        await flush_processor()
        node = TransitNode("tn2", best_location=loc, poi=poi, has_car=False, max_walk=30)
        a = await node.attempt()
        assert a.pending


class TestWalkLegCheckNode:
    @pytest.mark.asyncio
    async def test_false_when_no_transit(self):
        from houses.commute import CostGroup
        from houses.nodes.transit import WalkLegCheckNode

        transit = UserInputNode[Commute]("transit_w", Commute)
        commute = Commute(
            person=Person("Simon", has_car=True),
            label="Office",
            destination=PlaceOfInterest("Office", "SW1V 2QQ"),
            duration=Quantity(30, "minute"),
            daily_cost=Money("0", "GBP"),
            details=(CostGroup(legs=(), operator="", cost=None),),  # no legs → no walk
        )
        node = WalkLegCheckNode("wlc", transit_node=transit, max_walk=30)
        transit.push(commute, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() is False


class TestTransitNodeJson:
    @pytest.mark.asyncio
    async def test_to_json_has_boolean_fields(self):
        """TransitNode.to_json() must include succeeded/pending/impossible booleans."""
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc_tj", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi_tj", PlaceOfInterest)

        node = TransitNode("tn_json", best_location=loc, poi=poi, has_car=False, max_walk=30)
        j = await node.to_json()
        assert "succeeded" in j, "Missing succeeded field"
        assert "pending" in j, "Missing pending field"
        assert "impossible" in j, "Missing impossible field"
        assert j["pending"] is True, "Should be pending (no deps pushed)"
        assert j["succeeded"] is False
        assert j["impossible"] is False
        assert j["status"] == "pending"


    @pytest.mark.asyncio
    async def test_provenance_includes_input_values(self):
        """When a transit commute fails, the provenance should include the
        input values (origin coordinates, destination postcode) so developers
        can see what inputs caused the failure without tracing code.

        This relies on UserInputNode.build_provenance() including value,
        and Provenance.to_dict() serializing it.
        """
        from houses.geo import GeoPoint
        from houses.model.domain import PlaceOfInterest
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc_ti", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi_ti", PlaceOfInterest)

        node = TransitNode("tn_inputs", best_location=loc, poi=poi, has_car=False, max_walk=30)

        loc.push(GeoPoint(51.6, -1.25), "test")
        poi.push(PlaceOfInterest(label="Aldgate", postcode="EC3A 7LP"), "test")

        await node.refresh()
        j = await node.to_json()

        # The provenance should contain the dependency values
        prov = j.get("provenance", {})
        sources = prov.get("sources", {})

        # Check the POI source includes its value (the postcode)
        poi_source = sources.get("poi_ti", {})
        assert "value" in poi_source, (
            f"POI provenance should include 'value' with the postcode. "
            f"Got keys: {list(poi_source.keys())}"
        )

        # Check the location source includes its value (the GeoPoint)
        loc_source = sources.get("loc_ti", {})
        assert "value" in loc_source, (
            f"Location provenance should include 'value' with the GeoPoint. "
            f"Got keys: {list(loc_source.keys())}"
        )