from __future__ import annotations

import pytest
from money import Money

from dag.attempt import Provenance
from dag.source_node import SourceNode
from houses.commute import CostGroup
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest


class TestCommuteSelectorNode:
    @pytest.mark.asyncio
    async def test_transit_takes_priority(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_source_node

        origin = SourceNode[GeoPoint]("origin", GeoPoint)
        poi = SourceNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_source_node("transit")
        bus = commute_source_node("bus")

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
        )

        origin.push(GeoPoint(51.5, -0.1), Provenance("user"))
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, Provenance("config"))

        transit_commute = _make_commute(duration_min=32, cost_gbp=4.50)
        bus_commute = _make_commute(duration_min=55, cost_gbp=2.00)

        transit.push(transit_commute, Provenance("TfL"))
        bus.push(bus_commute, Provenance("Bus"))

        a = await node.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == transit_commute

    @pytest.mark.asyncio
    async def test_fallback_to_bus(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_source_node

        origin = SourceNode[GeoPoint]("origin", GeoPoint)
        poi = SourceNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_source_node("transit")
        bus = commute_source_node("bus")

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
        )

        origin.push(GeoPoint(51.5, -0.1), Provenance("user"))
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, Provenance("config"))

        bus_commute = _make_commute(duration_min=55, cost_gbp=2.00)
        bus.push(bus_commute, Provenance("Bus"))

        a = await node.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == bus_commute

    @pytest.mark.asyncio
    async def test_impossible_when_both_fail(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_source_node

        origin = SourceNode[GeoPoint]("origin", GeoPoint)
        poi = SourceNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_source_node("transit")
        bus = commute_source_node("bus")

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
        )

        origin.push(GeoPoint(51.5, -0.1), Provenance("user"))
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, Provenance("config"))

        a = await node.attempt()
        assert not a.is_succeeded
        assert "transit_result" in a._error
        assert "bus_result" in a._error

    @pytest.mark.asyncio
    async def test_impossible_when_origin_missing(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_source_node

        origin = SourceNode[GeoPoint]("origin", GeoPoint)
        poi = SourceNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_source_node("transit")
        bus = commute_source_node("bus")

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
        )

        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, Provenance("config"))

        a = await node.attempt()
        assert not a.is_succeeded
        assert "origin" in a._error

    @pytest.mark.asyncio
    async def test_recomputes_when_transit_updates(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_source_node

        origin = SourceNode[GeoPoint]("origin", GeoPoint)
        poi = SourceNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_source_node("transit")
        bus = commute_source_node("bus")

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
        )

        origin.push(GeoPoint(51.5, -0.1), Provenance("user"))
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi, Provenance("config"))
        bus.push(_make_commute(duration_min=55, cost_gbp=2.00), Provenance("Bus"))

        assert (await node.attempt()).value_or_none().daily_cost == Money("2.00", "GBP")

        transit.push(_make_commute(duration_min=32, cost_gbp=4.50), Provenance("TfL"))
        assert (await node.attempt()).value_or_none().daily_cost == Money("4.50", "GBP")

    @pytest.mark.asyncio
    async def test_to_json_shape(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_source_node

        origin = SourceNode[GeoPoint]("origin", GeoPoint)
        poi = SourceNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_source_node("transit")
        bus = commute_source_node("bus")

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
        )

        origin.push(GeoPoint(51.5, -0.1), Provenance("user"))
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), Provenance("config"))
        transit.push(_make_commute(duration_min=32, cost_gbp=4.50), Provenance("TfL"))

        j = await node.to_json()
        assert j["succeeded"] is True
        assert j["value"] is not None
        assert j["error"] is None


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
        details=(CostGroup(legs=(), operator="TfL", cost=Money(str(cost_gbp), "GBP")),),
    )
