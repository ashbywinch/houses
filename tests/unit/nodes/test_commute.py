from __future__ import annotations

import pytest
from money import Money
from dag.derived_node import flush_processor

from dag.user_input_node import UserInputNode
from houses.commute import CostGroup
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest


class TestCommuteSelectorNode:
    @pytest.mark.asyncio
    async def test_transit_takes_priority(self):
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = commute_input_node("transit")
        bus = commute_input_node("bus")

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
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

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
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

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
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

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
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

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
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

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
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
