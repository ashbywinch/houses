"""Tests for bus route, fare, and leg augmentation DAG nodes."""

from __future__ import annotations

from typing import override

import pytest
from money import Money
from pint import Quantity

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.bus_journey import BusJourneyRegistry
from houses.geo import GeoPoint


def _mw(value: int):
    """A fixed max-walk input node."""
    from dag.user_input_node import UserInputNode

    node = UserInputNode("_mw", int)
    node.push(value, "test")
    return node


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
        a = await node.attempt()
        assert a.succeeded


class _StubFareReader(BusJourneyRegistry):
    """A BusJourneyRegistry that returns known fares without loading real data."""

    def __init__(self):
        super().__init__()
        self._loaded = True
        self._data = {}
        self._meta = {}

    @override
    def _load(self):
        pass

    @override
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
    async def test_constructs_without_max_walk_node(self):
        """max_walk_node is optional — the dep_names must shrink with the
        deps, not force a 4-vs-3 ValueError at construction."""
        from houses.model.domain import Commute
        from houses.nodes.bus import BusLegAugmentNode

        transit = UserInputNode[Commute]("t_bl_nmw", Commute)
        route = UserInputNode[dict]("r_bl_nmw", dict)
        fare = UserInputNode[dict]("f_bl_nmw", dict)

        node = BusLegAugmentNode(
            "bla_nmw",
            transit_input=transit,
            bus_route_node=route,
            bods_fare_node=fare,
        )
        assert node._dep_names == ("transit_attempt", "bus_route_attempt", "bods_fare_attempt")

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
            max_walk_node=_mw(30),
        )
        commute = Commute(
            person=Person(name="", has_car=False),
            label="Test",
            destination=PlaceOfInterest(label="", address=""),
            duration=Quantity(30, "minute"),
            daily_cost=Money("0", "GBP"),
            mode="transit",
            _details=(CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration=Quantity(5, "minute")),)),),
        )
        transit.push(commute, "test")
        route.push({}, "test")
        fare.push({}, "test")
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
            max_walk_node=_mw(30),
        )
        commute = Commute(
            person=Person(name="", has_car=False),
            label="Test",
            destination=PlaceOfInterest(label="", address=""),
            duration=Quantity(90, "minute"),
            daily_cost=Money("12.50", "GBP"),
            mode="transit",
            _details=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration=Quantity(46, "minute")),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(42, "minute")),)),
            ),
        )
        transit.push(commute, "test")
        # Empty route data — no bus stops
        route.push({"bus_stops": []}, "test")
        fare.push({}, "test")
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
            max_walk_node=_mw(30),
        )
        commute = Commute(
            person=Person(name="", has_car=False),
            label="L",
            destination=PlaceOfInterest(label="L", address="EC3A 7LP"),
            duration=Quantity(90, "minute"),
            daily_cost=Money("12.50", "GBP"),
            mode="transit",
            _details=(
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
            max_walk_node=_mw(30),
        )
        commute = Commute(
            person=Person(name="", has_car=False),
            label="L",
            destination=PlaceOfInterest(label="L", address="EC3A 7LP"),
            duration=Quantity(90, "minute"),
            daily_cost=Money("12.50", "GBP"),
            mode="transit",
            _details=(
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
            max_walk_node=_mw(30),
        )
        commute = Commute(
            person=Person(name="", has_car=False),
            label="L",
            destination=PlaceOfInterest(label="L", address="EC3A 7LP"),
            duration=Quantity(90, "minute"),
            daily_cost=Money("12.50", "GBP"),
            mode="transit",
            _details=(
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
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == commute


class TestBusLegAugmentInfeasible:
    """An infeasible transit commute (no route) passes through the bus
    augment untouched — no .details access on an infeasible commute."""

    @pytest.mark.asyncio
    async def test_infeasible_transit_passes_through(self):
        from dag.attempt import Attempt
        from dag.derived_node import DerivedNode
        from houses.model.domain import Commute, Person, PlaceOfInterest
        from houses.nodes.bus import BusLegAugmentNode

        class _FixedCommute(DerivedNode[Commute]):
            def __init__(self, node_id: str, commute: Commute):
                super().__init__(node_id, Commute, ())
                self._att = Attempt.succeeded(commute)

            @override
            async def attempt(self):
                return self._att

            @override
            def latest_attempt(self):
                return self._att

            @override
            def compute(self, *dep_attempts):
                raise AssertionError("fixed node should not compute")

        infeasible = Commute(
            person=Person(name="", has_car=False),
            label="NoRoute",
            destination=PlaceOfInterest(label="", address=""),
            duration=Quantity(0, "minute"),  # type: ignore[arg-type]
            daily_cost=Money("0", "GBP"),
            mode="transit",
            _details=(),
            infeasible=True,
        )
        transit = _FixedCommute("bl_ir_inf", infeasible)
        route = UserInputNode[dict]("bl_ir_route", dict)
        route.push({}, "test")
        fare = UserInputNode[dict]("bl_ir_fare", dict)
        fare.push({}, "test")

        node = BusLegAugmentNode(
            "bl_ir",
            transit_input=transit,
            bus_route_node=route,
            bods_fare_node=fare,
            max_walk_node=_mw(30),
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"infeasible transit must pass through, got: {a.status}: {a.error}"
        _v = a.value_or_none()
        assert _v is not None
        assert _v.infeasible


class TestBusFallbackForNoTflRoute:
    """When TfL has no route, the Google Routes bus lookup must still be
    consumed — a 13-minute non-TfL bus is a real commute option."""

    @pytest.mark.asyncio
    async def test_infeasible_transit_with_bus_route_builds_bus_commute(self):
        from dag.attempt import Attempt
        from dag.derived_node import DerivedNode
        from houses.model.domain import Commute, Person, PlaceOfInterest
        from houses.nodes.bus import BusLegAugmentNode

        class _FixedTransit(DerivedNode[Commute]):
            def __init__(self, node_id: str, commute: Commute):
                super().__init__(node_id, Commute, ())
                self._att = Attempt.succeeded(commute)

            @override
            async def attempt(self):
                return self._att

            @override
            def latest_attempt(self):
                return self._att

            @override
            def compute(self, *dep_attempts):
                raise AssertionError("fixed node should not compute")

        infeasible = Commute(
            person=Person(name="George", has_car=False, is_child=True),
            label="Secondary School",
            destination=PlaceOfInterest(label="Secondary School", address="51.6053205,-1.2749334"),
            duration=Quantity(0, "minute"),  # type: ignore[arg-type]
            daily_cost=Money("0", "GBP"),
            mode="transit",
            _details=(),
            infeasible=True,
        )
        transit = _FixedTransit("bfn_inf", infeasible)

        route = UserInputNode[dict]("bfn_route", dict)
        route.push(
            {
                "bus_stops": [
                    {
                        "departure_name": "Tyrrells Close",
                        "arrival_name": "Great Western Park ASDA",
                        "departure_lat": 51.596798,
                        "departure_lon": -1.294132,
                        "arrival_lat": 51.604454,
                        "arrival_lon": -1.271184,
                    }
                ],
                "duration_minutes": 13,
            },
            "test",
        )
        fare = UserInputNode[dict]("bfn_fare", dict)
        fare.push({"stop_fares": {}}, "test")

        node = BusLegAugmentNode(
            "bfn",
            transit_input=transit,
            bus_route_node=route,
            bods_fare_node=fare,
            max_walk_node=_mw(30),
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"bus fallback should build a commute, got: {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        assert val.duration.magnitude == 13
        assert not val.infeasible


class TestBusAugmentLateRoute:
    """The bus route/fare are conditional deps — but they must be STATIC
    deps too, so their changed signals re-schedule the augment when they
    resolve after its first (blocked) refresh.  Without the static wiring
    a late-arriving route leaves the augment pending forever."""

    @pytest.mark.asyncio
    async def test_late_route_and_fare_re_schedule_augment(self):
        from dag.attempt import Attempt
        from dag.derived_node import DerivedNode
        from houses.model.domain import Commute, Person, PlaceOfInterest
        from houses.nodes.bus import BusLegAugmentNode

        class _FixedTransit(DerivedNode[Commute]):
            def __init__(self, node_id: str, commute: Commute):
                super().__init__(node_id, Commute, ())
                self._att = Attempt.succeeded(commute)

            @override
            async def attempt(self):
                return self._att

            @override
            def latest_attempt(self):
                return self._att

            @override
            def compute(self, *dep_attempts):
                raise AssertionError("fixed node should not compute")

        infeasible = Commute(
            person=Person(name="George", has_car=False, is_child=True),
            label="Secondary School",
            destination=PlaceOfInterest(label="Secondary School", address="51.6053205,-1.2749334"),
            duration=Quantity(0, "minute"),  # type: ignore[arg-type]
            daily_cost=Money("0", "GBP"),
            mode="transit",
            _details=(),
            infeasible=True,
        )
        transit = _FixedTransit("blr_inf", infeasible)

        # Route and fare are NOT pushed yet — pending at the first flush.
        route = UserInputNode[dict]("blr_route", dict)
        fare = UserInputNode[dict]("blr_fare", dict)
        node = BusLegAugmentNode(
            "blr",
            transit_input=transit,
            bus_route_node=route,
            bods_fare_node=fare,
            max_walk_node=_mw(30),
        )

        await flush_processor()
        # First refresh found the bus route pending → the augment must
        # still be pending (blocked), NOT resolved as failed.
        assert (await node.attempt()).pending, "augment must wait for the bus route"

        # The route arrives LATER — its changed signal must re-schedule
        # the augment (this is the regression: it used to stay pending).
        route.push(
            {
                "bus_stops": [
                    {
                        "departure_name": "Tyrrells Close",
                        "arrival_name": "Great Western Park ASDA",
                        "departure_lat": 51.596798,
                        "departure_lon": -1.294132,
                        "arrival_lat": 51.604454,
                        "arrival_lon": -1.271184,
                    }
                ],
                "duration_minutes": 13,
            },
            "test",
        )
        await flush_processor()
        fare.push({"stop_fares": {}}, "test")
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, f"late bus route must resolve the augment, got: {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        assert val.duration.magnitude == 13
        assert not val.infeasible
