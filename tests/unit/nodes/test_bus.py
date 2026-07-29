"""Tests for bus route, fare, and leg augmentation DAG nodes."""

from __future__ import annotations

import pytest

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.bus_journey import BusJourneyRegistry
from houses.geo import GeoPoint


class TestBusRouteNode:
    @pytest.mark.asyncio
    async def test_returns_dict_when_location_ok(self):
        from houses.nodes.bus import BusRouteNode

        loc = UserInputNode[GeoPoint]("loc_br", GeoPoint)
        dest = UserInputNode[str]("dest_br", str)

        async def fake_google_routes(body, field_mask, **kw):
            return {
                "routes": [
                    {
                        "duration": "600s",
                        "legs": [
                            {
                                "steps": [
                                    {
                                        "travelMode": "TRANSIT",
                                        "transitDetails": {
                                            "transitLine": {"vehicle": {"type": "BUS"}},
                                            "stopDetails": {
                                                "departureStop": {
                                                    "name": "Stop A",
                                                    "location": {"latLng": {"latitude": 51.5, "longitude": -0.1}},
                                                },
                                                "arrivalStop": {
                                                    "name": "Stop B",
                                                    "location": {"latLng": {"latitude": 51.51, "longitude": -0.11}},
                                                },
                                            },
                                        },
                                    },
                                ]
                            }
                        ],
                    }
                ],
            }

        node = BusRouteNode(
            "br",
            best_location=loc,
            poi=dest,
            _google_routes_post=fake_google_routes,
        )
        loc.push(GeoPoint(51.5, -0.1), "test")
        dest.push("EC3A 7LP", "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded


class _StubFareReader(BusJourneyRegistry):
    """A BusJourneyRegistry that returns known fares without loading real data."""

    def __init__(self):
        self._loaded = True
        self._data = {}
        self._meta = {}

    def _load(self):
        pass

    def fares_for_stops(self, dep_stop_name, arr_stop_name, dep_point=None, arr_point=None):
        from money import Money

        from houses.bus_journey import FareProduct, FareProductType

        return {
            FareProductType.SINGLE: FareProduct(
                type=FareProductType.SINGLE,
                price=Money("2.00", "GBP"),
                operator="TestBus",
                zone_pair="Z1:Z1",
            ),
        }


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

    @pytest.mark.asyncio
    async def test_looks_up_fares_for_stops(self):
        """BodsFareNode returns stop fares for bus stops in the route."""
        from houses.nodes.bus import BodsFareNode
        from houses.services_provider import _request_services
        from tests.helpers import make_services

        svc = make_services(bus_fare_registry=_StubFareReader())
        token = _request_services.set(svc)
        try:
            route = UserInputNode[dict]("route_bf2", dict)
            node = BodsFareNode("bf2", bus_route_node=route)

            route.push(
                {
                    "bus_stops": [
                        {
                            "departure_name": "Stop A",
                            "arrival_name": "Stop B",
                            "departure_lat": 51.5,
                            "departure_lon": -0.1,
                            "arrival_lat": 51.51,
                            "arrival_lon": -0.11,
                        },
                    ],
                    "duration_minutes": 15,
                },
                "test",
            )
            await flush_processor()
            await flush_processor()
            a = await node.attempt()
            assert a.succeeded, f"Expected succeeded, got {a.status}: {a.error}"
            val = a.value_or_none()
            assert val is not None
            assert val["stop_fares"] == {
                "Stop A": {"amount": "4.00", "currency": "GBP"},
            }
        finally:
            _request_services.reset(token)


class TestBusLegAugmentNode:
    @pytest.mark.asyncio
    async def test_walk_short_no_replacement(self):
        """When walk is shorter than max_walk, commute is returned unchanged."""
        from money import Money
        from pint import Quantity

        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.model.domain import Commute, Person, PlaceOfInterest
        from houses.nodes.bus import BusLegAugmentNode

        transit = UserInputNode[Commute]("t_bl_short", Commute)
        route = UserInputNode[dict]("r_bl_short", dict)
        fare = UserInputNode[dict]("f_bl_short", dict)

        node = BusLegAugmentNode(
            "bla_short",
            transit_input=transit,
            bus_route_node=route,
            bods_fare_node=fare,
            max_walk=30,
        )
        commute = Commute(
            person=Person(name="", has_car=False),
            label="Test",
            destination=PlaceOfInterest(label="", address=""),
            duration=Quantity(30, "minute"),
            daily_cost=Money("0", "GBP"),
            mode="transit",
            details=(CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration=Quantity(5, "minute")),)),),
        )
        transit.push(commute, "test")
        route.push({}, "test")
        fare.push({}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == commute

    @pytest.mark.asyncio
    async def test_no_bus_route_no_replacement(self):
        """When no bus route data, the commute is returned unchanged."""
        from money import Money
        from pint import Quantity

        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.model.domain import Commute, Person, PlaceOfInterest
        from houses.nodes.bus import BusLegAugmentNode

        transit = UserInputNode[Commute]("t_bl_nobus", Commute)
        route = UserInputNode[dict]("r_bl_nobus", dict)
        fare = UserInputNode[dict]("f_bl_nobus", dict)

        node = BusLegAugmentNode(
            "bla_nobus",
            transit_input=transit,
            bus_route_node=route,
            bods_fare_node=fare,
            max_walk=30,
        )
        commute = Commute(
            person=Person(name="", has_car=False),
            label="Test",
            destination=PlaceOfInterest(label="", address=""),
            duration=Quantity(90, "minute"),
            daily_cost=Money("12.50", "GBP"),
            mode="transit",
            details=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration=Quantity(46, "minute")),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(42, "minute")),)),
            ),
        )
        transit.push(commute, "test")
        # Empty route data — no bus stops
        route.push({"bus_stops": []}, "test")
        fare.push({}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == commute

    @pytest.mark.asyncio
    async def test_replaces_long_walk_with_bus(self):
        """When walk is too long and bus route available, walk is replaced."""
        from money import Money
        from pint import Quantity

        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.model.domain import Commute, Person, PlaceOfInterest
        from houses.nodes.bus import BusLegAugmentNode

        transit = UserInputNode[Commute]("t_bl_repl", Commute)
        route = UserInputNode[dict]("r_bl_repl", dict)
        fare = UserInputNode[dict]("f_bl_repl", dict)

        node = BusLegAugmentNode(
            "bla_repl",
            transit_input=transit,
            bus_route_node=route,
            bods_fare_node=fare,
            max_walk=30,
        )
        commute = Commute(
            person=Person(name="", has_car=False),
            label="L",
            destination=PlaceOfInterest(label="L", address="EC3A 7LP"),
            duration=Quantity(90, "minute"),
            daily_cost=Money("12.50", "GBP"),
            mode="transit",
            details=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration=Quantity(46, "minute")),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(42, "minute")),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TUBE, duration=Quantity(4, "minute")),)),
            ),
        )
        transit.push(commute, "test")
        route.push(
            {
                "bus_stops": [
                    {
                        "departure_name": "Stop A",
                        "arrival_name": "Stop B",
                        "departure_lat": None,
                        "departure_lon": None,
                        "arrival_lat": None,
                        "arrival_lon": None,
                    },
                ],
                "duration_minutes": 15,
            },
            "test",
        )
        fare.push(
            {
                "stop_fares": {"Stop A": {"amount": "1.90", "currency": "GBP"}},
            },
            "test",
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, f"got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None

        # Walk leg should be replaced with BUS
        first_leg = val.details[0].legs[0]
        assert first_leg.mode == LegMode.BUS, f"Expected first leg BUS, got {first_leg.mode}"
        assert int(first_leg.duration.magnitude) == 15

        # Bus CostGroup should carry the fare (BodsFareNode returns round-trip amount)
        assert val.details[0].cost is not None
        assert float(val.details[0].cost.amount) == 1.90
        # Duration: 90 - 46 + 15 = 59
        assert val.duration.magnitude == 59

        # Cost: original 12.50 + bus 1.90 = 14.40
        assert float(val.daily_cost.amount) == 14.40

    @pytest.mark.asyncio
    async def test_no_walk_leg_remains_after_replacement(self):
        """No WALK leg should remain after bus replaces the walk."""
        from money import Money
        from pint import Quantity

        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.model.domain import Commute, Person, PlaceOfInterest
        from houses.nodes.bus import BusLegAugmentNode

        transit = UserInputNode[Commute]("t_bl_nw", Commute)
        route = UserInputNode[dict]("r_bl_nw", dict)
        fare = UserInputNode[dict]("f_bl_nw", dict)

        node = BusLegAugmentNode(
            "bla_nw",
            transit_input=transit,
            bus_route_node=route,
            bods_fare_node=fare,
            max_walk=30,
        )
        commute = Commute(
            person=Person(name="", has_car=False),
            label="L",
            destination=PlaceOfInterest(label="L", address="EC3A 7LP"),
            duration=Quantity(90, "minute"),
            daily_cost=Money("12.50", "GBP"),
            mode="transit",
            details=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration=Quantity(46, "minute")),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(42, "minute")),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TUBE, duration=Quantity(4, "minute")),)),
            ),
        )
        transit.push(commute, "test")
        route.push(
            {
                "bus_stops": [
                    {
                        "departure_name": "Stop A",
                        "arrival_name": "Stop B",
                        "departure_lat": None,
                        "departure_lon": None,
                        "arrival_lat": None,
                        "arrival_lon": None,
                    },
                ],
                "duration_minutes": 15,
            },
            "test",
        )
        fare.push(
            {
                "stop_fares": {"Stop A": {"amount": "1.90", "currency": "GBP"}},
            },
            "test",
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None

        # No WALK leg should remain
        for cg in val.details:
            for leg in cg.legs:
                assert leg.mode != LegMode.WALK, f"WALK leg should have been replaced but found {leg}"

    @pytest.mark.asyncio
    async def test_walk_under_threshold_no_replacement(self):
        """When walk is under max_walk, no replacement happens even with bus."""
        from money import Money
        from pint import Quantity

        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.model.domain import Commute, Person, PlaceOfInterest
        from houses.nodes.bus import BusLegAugmentNode

        transit = UserInputNode[Commute]("t_bl_under", Commute)
        route = UserInputNode[dict]("r_bl_under", dict)
        fare = UserInputNode[dict]("f_bl_under", dict)

        node = BusLegAugmentNode(
            "bla_under",
            transit_input=transit,
            bus_route_node=route,
            bods_fare_node=fare,
            max_walk=30,
        )
        commute = Commute(
            person=Person(name="", has_car=False),
            label="L",
            destination=PlaceOfInterest(label="L", address="EC3A 7LP"),
            duration=Quantity(90, "minute"),
            daily_cost=Money("12.50", "GBP"),
            mode="transit",
            details=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration=Quantity(9, "minute")),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(42, "minute")),)),
            ),
        )
        transit.push(commute, "test")
        route.push(
            {
                "bus_stops": [
                    {
                        "departure_name": "Stop A",
                        "arrival_name": "Stop B",
                        "departure_lat": None,
                        "departure_lon": None,
                        "arrival_lat": None,
                        "arrival_lon": None,
                    },
                ],
                "duration_minutes": 15,
            },
            "test",
        )
        fare.push({}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == commute
