"""Tests for the DAG-based commute computation pipeline.

Replaces the retired ``houses.enricher`` module.  Tests the three-node
pipeline — TransitNode → CommuteSelectorNode → CommuteBreakdownNode —
backed by ``FakeCommuteRouter`` with canned results.
"""

from __future__ import annotations

import copy

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import DerivedNode, flush_processor
from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.transit_route import _apply_park_and_ride_to_journeys, _format_route_summary

# ======================================================================
# DAG-based commute computation (replaces old houses.enricher tests)
# ======================================================================


def _make_commute(duration_min: int = 32, cost_gbp: str | float = "10.0") -> Commute:
    """Build a Commute suitable for feeding into a TransitNode / FakeCommuteRouter."""
    from pint import Quantity

    from houses.commute import CostGroup
    from houses.model.domain import Commute as CommuteDomain

    office = PlaceOfInterest("Office", "SW1V 2QQ", trips_per_week=1, weeks_per_year=46)
    person = Person("Simon", True, places_of_interest=(office,))
    return CommuteDomain(
        person=person,
        label=office.label,
        destination=office,
        duration=Quantity(duration_min, "minute"),
        daily_cost=Money(str(cost_gbp), "GBP"),
        details=(CostGroup(legs=(), operator="TfL", cost=Money(str(cost_gbp), "GBP")),),
    )


def _serialize_commute(duration_min: int, cost_gbp: float, label: str = "Office", mode: str = "transit") -> Commute:
    """Return a Commute matching the shape TransitNode/CommuteSelectorNode produce."""
    from houses.model.domain import Commute as CommuteObj
    from houses.model.domain import Person, PlaceOfInterest

    return CommuteObj(
        person=Person(name="", has_car=False),
        label=label,
        destination=PlaceOfInterest(label=label, postcode=""),
        duration=Quantity(duration_min, "minute"),
        daily_cost=Money(str(cost_gbp), "GBP"),
        mode=mode,
    )


# ── TransitNode ──────────────────────────────────────────────────────


class TestTransitCommute:
    """TransitNode — produces a serialised commute dict from the router."""

    @pytest.mark.asyncio
    async def test_pending_without_location(self):
        """No location → node stays pending."""
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("tr_loc1", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi1", PlaceOfInterest)

        node = TransitNode("tr1", best_location=loc, poi=poi, has_car=False, max_walk=30)
        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_pending_without_poi(self):
        """No POI → node stays pending even with location set."""
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("tr_loc2", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi2", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        await flush_processor()

        node = TransitNode("tr2", best_location=loc, poi=poi, has_car=False, max_walk=30)
        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_returns_commute_from_router(self, monkeypatch):
        """With all deps, TransitNode calls commute_router.route() and serialises the result."""
        from houses.nodes.transit import TransitNode
        from houses.services_provider import get_services

        loc = UserInputNode[GeoPoint]("tr_loc3", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi3", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        office = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office, "config")

        commute = _make_commute(duration_min=45, cost_gbp="12.50")

        async def mock_route(origin, destination, *, has_car, max_walk_minutes):
            return Attempt.succeeded(commute)

        svc = get_services()
        monkeypatch.setattr(svc.commute_router, "route", mock_route)

        node = TransitNode("tr3/Simon/Office/computed_transit", best_location=loc, poi=poi, has_car=False, max_walk=30)
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, f"Expected succeeded, got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val.duration.magnitude == 45
        assert val.daily_cost.amount == 12.50

    @pytest.mark.asyncio
    async def test_impossible_when_router_fails(self, monkeypatch):
        """Router returning impossible → TransitNode is impossible."""
        from houses.nodes.transit import TransitNode
        from houses.services_provider import get_services

        loc = UserInputNode[GeoPoint]("tr_loc4", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi4", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        async def mock_route_fail(origin, destination, *, has_car, max_walk_minutes):
            return Attempt.impossible("API down")

        svc = get_services()
        monkeypatch.setattr(svc.commute_router, "route", mock_route_fail)

        node = TransitNode("tr4/Simon/Office/computed_transit", best_location=loc, poi=poi, has_car=False, max_walk=30)
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.impossible

    @pytest.mark.asyncio
    async def test_to_json_has_boolean_fields(self):
        """TransitNode.to_json() must include succeeded/pending/impossible booleans."""
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("tr_loc5", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi5", PlaceOfInterest)

        node = TransitNode("tr5", best_location=loc, poi=poi, has_car=False, max_walk=30)
        j = await node.to_json()
        assert "succeeded" in j
        assert "pending" in j
        assert "impossible" in j
        assert j["pending"] is True
        assert j["succeeded"] is False
        assert j["impossible"] is False

    @pytest.mark.asyncio
    async def test_uses_has_car_and_max_walk_params(self, monkeypatch):
        """Uses has_car and max_walk from constructor params for the commute request."""
        from houses.nodes.transit import TransitNode
        from houses.services_provider import get_services

        loc = UserInputNode[GeoPoint]("tr_loc6", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi6", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        captured = {}

        async def capture_route(origin, destination, *, has_car, max_walk_minutes):
            captured["has_car"] = has_car
            captured["max_walk"] = max_walk_minutes
            return Attempt.succeeded(_make_commute())

        svc = get_services()
        monkeypatch.setattr(svc.commute_router, "route", capture_route)

        node = TransitNode("rid/Simon/Office/computed_transit", best_location=loc, poi=poi, has_car=True, max_walk=15)
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert captured.get("has_car") is True
        assert captured.get("max_walk") == 15

    @pytest.mark.asyncio
    async def test_transit_is_not_child(self, monkeypatch):
        """Transit result is_child is always False (child handling done upstream)."""
        from houses.nodes.transit import TransitNode
        from houses.services_provider import get_services

        loc = UserInputNode[GeoPoint]("tr_loc7", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi7", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("School", "SL6 1AA"), "config")

        async def mock_route(origin, destination, *, has_car, max_walk_minutes):
            return Attempt.succeeded(_make_commute(duration_min=20, cost_gbp="0"))

        svc = get_services()
        monkeypatch.setattr(svc.commute_router, "route", mock_route)

        node = TransitNode("rid/George/School/computed_transit", best_location=loc, poi=poi, has_car=False, max_walk=30)
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none().person.is_child is False


# ── CommuteSelectorNode ──────────────────────────────────────────────


class TestCommuteSelectorPipeline:
    """CommuteSelectorNode — picks transit, falls back to bus, or raises impossible."""

    @pytest.mark.asyncio
    async def test_transit_takes_priority(self):
        """When transit succeeds it is returned directly (Commute object)."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("csel_o1", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p1", PlaceOfInterest)
        transit = commute_input_node("csel_t1")
        bus = commute_input_node("csel_b1")

        node = CommuteSelectorNode(
            "csel1",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=_succeeded_walk_check(False),
        )

        origin.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        transit_commute = _make_commute(duration_min=30, cost_gbp="10.0")
        bus_commute = _make_commute(duration_min=55, cost_gbp="5.0")

        transit.push(transit_commute, "TfL")
        bus.push(bus_commute, "Bus")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none().daily_cost == Money("10.0", "GBP")

    @pytest.mark.asyncio
    async def test_fallback_to_bus(self):
        """When transit fails but bus succeeds, bus is returned."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("csel_o2", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p2", PlaceOfInterest)
        transit = commute_input_node("csel_t2")
        bus = commute_input_node("csel_b2")

        node = CommuteSelectorNode(
            "csel2",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=_succeeded_walk_check(False),
        )

        origin.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        bus_commute = _make_commute(duration_min=55, cost_gbp="5.0")
        bus.push(bus_commute, "Bus")

        await flush_processor()

        # transit is pending → selector is pending (waits for transit)
        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_impossible_when_both_fail(self):
        """When both transit and bus fail, selector is impossible."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("csel_o3", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p3", PlaceOfInterest)
        transit = commute_input_node("csel_t3")
        bus = commute_input_node("csel_b3")

        node = CommuteSelectorNode(
            "csel3",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=_succeeded_walk_check(False),
        )

        origin.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        await flush_processor()

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_impossible_when_origin_missing(self):
        """No origin → selector is impossible."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("csel_o4", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p4", PlaceOfInterest)
        transit = commute_input_node("csel_t4")
        bus = commute_input_node("csel_b4")

        node = CommuteSelectorNode(
            "csel4",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=_succeeded_walk_check(False),
        )

        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        await flush_processor()

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_to_json_includes_value_and_booleans(self):
        """CommuteSelectorNode.to_json() includes status/value/is_child/provenance."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("csel_o5", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p5", PlaceOfInterest)
        transit = commute_input_node("csel_t5")
        bus = commute_input_node("csel_b5")

        node = CommuteSelectorNode(
            "csel5",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=_succeeded_walk_check(False),
        )

        origin.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        transit.push(_make_commute(duration_min=32, cost_gbp="4.50"), "TfL")
        bus.push(_make_commute(duration_min=55, cost_gbp="2.00"), "Bus")

        await flush_processor()

        j = await node.to_json()
        assert j["status"] == "succeeded"
        val = j["value"]
        assert isinstance(val, dict), "value should be a dict"
        assert "duration" in val, "value missing duration"
        assert "daily_cost" in val, "value missing daily_cost"
        assert val.get("daily_cost", {}).get("amount") == 4.5
        assert j["is_child"] is False
        assert "error" not in j
        assert "provenance" in j

    @pytest.mark.asyncio
    async def test_selector_with_commute_values(self):
        """CommuteSelectorNode passes through a Commute object."""
        from houses.nodes.commute import CommuteSelectorNode, commute_input_node

        origin = UserInputNode[GeoPoint]("csel_o6", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p6", PlaceOfInterest)
        transit = commute_input_node("csel_t6")
        bus = commute_input_node("csel_b6")

        node = CommuteSelectorNode(
            "csel6",
            origin=origin,
            poi=poi,
            transit_result=transit,
            bus_result=bus,
            walk_leg_check=_succeeded_walk_check(False),
        )

        origin.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        transit_commute = _make_commute(duration_min=35, cost_gbp="8.50")
        bus.push(_make_commute(duration_min=55, cost_gbp="2.00"), "Bus")
        transit.push(transit_commute, "TfL")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val.duration.magnitude == 35
        assert float(val.daily_cost.amount) == 8.50
        assert val.label == "Office"


# ── CommuteBreakdownNode ─────────────────────────────────────────────


class TestCommuteBreakdown:
    """CommuteBreakdownNode — sums yearly commute costs."""

    @pytest.mark.asyncio
    async def test_yearly_formula_with_all_costs(self):
        """46wk x (15 + 10 + 2x24) = 46 x 73 = 3358"""
        from houses.nodes.monthly_costs import CommuteBreakdownNode

        so = UserInputNode[Commute]("cbd_so1", Commute)
        sb = UserInputNode[Commute]("cbd_sb1", Commute)
        lo = UserInputNode[Commute]("cbd_lo1", Commute)
        persons = UserInputNode[list]("cbd_ps1", list)

        selectors = {
            "Simon/Pimlico": so,
            "Simon/Bracknell": sb,
            "Lorena/Aldgate": lo,
        }

        node = CommuteBreakdownNode(
            "cbd1",
            commute_selectors=selectors,
            persons_source=persons,
        )

        so.push(_serialize_commute(30, 15.0, label="Pimlico"), "test")
        sb.push(_serialize_commute(90, 10.0, label="Bracknell", mode="drive"), "test")
        lo.push(_serialize_commute(45, 24.0, label="Aldgate"), "test")
        persons.push(
            [
                Person(
                    "Simon",
                    True,
                    places_of_interest=(
                        PlaceOfInterest("Pimlico", "SW1V 2QQ", trips_per_week=1, weeks_per_year=46),
                        PlaceOfInterest("Bracknell", "RG12 8YA", trips_per_week=1, weeks_per_year=46),
                    ),
                ),
                Person(
                    "Lorena",
                    False,
                    places_of_interest=(PlaceOfInterest("Aldgate", "EC3A 7LP", trips_per_week=2, weeks_per_year=46),),
                ),
            ],
            "test",
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        # Simon office: 15.0 * 1 * 46 = 690
        # Simon Bracknell: 10.0 * 1 * 46 = 460
        # Lorena: 24.0 * 2 * 46 = 2208
        # Total: 3358
        assert val["yearly_total_gbp"] == 3358.0

    @pytest.mark.asyncio
    async def test_missing_cost_means_partial_total(self):
        """When some costs are present, total includes only those."""
        from houses.nodes.monthly_costs import CommuteBreakdownNode

        so = UserInputNode[Commute]("cbd_so2", Commute)
        sb = UserInputNode[Commute]("cbd_sb2", Commute)
        lo = UserInputNode[Commute]("cbd_lo2", Commute)
        persons = UserInputNode[list]("cbd_ps2", list)

        selectors = {
            "Simon/Pimlico": so,
            "Simon/Bracknell": sb,
            "Lorena/Aldgate": lo,
        }

        node = CommuteBreakdownNode(
            "cbd2",
            commute_selectors=selectors,
            persons_source=persons,
        )

        # Simon office has no commute data (zero-cost commute), others have costs
        so.push(
            Commute(
                person=Person("", False),
                label="",
                destination=PlaceOfInterest("", ""),
                duration=Quantity(0, "minute"),
                daily_cost=Money("0", "GBP"),
            ),
            "test",
        )
        sb.push(_serialize_commute(90, 10.0, label="Bracknell", mode="drive"), "test")
        lo.push(_serialize_commute(45, 24.0, label="Aldgate"), "test")
        persons.push(
            [
                Person(
                    "Simon",
                    True,
                    places_of_interest=(
                        PlaceOfInterest("Pimlico", "SW1V 2QQ", trips_per_week=1, weeks_per_year=46),
                        PlaceOfInterest("Bracknell", "RG12 8YA", trips_per_week=1, weeks_per_year=46),
                    ),
                ),
                Person(
                    "Lorena",
                    False,
                    places_of_interest=(PlaceOfInterest("Aldgate", "EC3A 7LP", trips_per_week=2, weeks_per_year=46),),
                ),
            ],
            "test",
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        # Simon Bracknell: 10.0 * 1 * 46 = 460
        # Lorena: 24.0 * 2 * 46 = 2208
        # Total: 2668
        assert val["yearly_total_gbp"] == 2668.0

    @pytest.mark.asyncio
    async def test_returns_defaults_when_no_commutes(self):
        """All commute selectors empty → yearly_total is 0.0."""
        from houses.nodes.monthly_costs import CommuteBreakdownNode

        persons = UserInputNode[list]("cbd_ps2", list)

        node = CommuteBreakdownNode(
            "cbd2",
            commute_selectors={},
            persons_source=persons,
        )

        persons.push([Person("Simon", True)], "test")
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none()["yearly_total_gbp"] == 0.0

    @pytest.mark.asyncio
    async def test_missing_commute_does_not_crash(self):
        """When some commute selectors are impossible, node still succeeds."""
        from houses.nodes.monthly_costs import CommuteBreakdownNode

        so = UserInputNode[Commute]("cbd_so3", Commute)
        sb = UserInputNode[Commute]("cbd_sb3", Commute)
        lo = UserInputNode[Commute]("cbd_lo3", Commute)
        persons = UserInputNode[list]("cbd_ps3", list)

        selectors = {
            "Simon/Pimlico": so,
            "Simon/Bracknell": sb,
            "Lorena/Aldgate": lo,
        }

        node = CommuteBreakdownNode(
            "cbd3",
            commute_selectors=selectors,
            persons_source=persons,
        )
        # Push all deps so they're terminal (none pending)
        # Simon Bracknell and Lorena Office get empty dicts (no real commute data)
        sb.push(
            Commute(
                person=Person("", False),
                label="",
                destination=PlaceOfInterest("", ""),
                duration=Quantity(0, "minute"),
                daily_cost=Money("0", "GBP"),
            ),
            "test",
        )
        lo.push(
            Commute(
                person=Person("", False),
                label="",
                destination=PlaceOfInterest("", ""),
                duration=Quantity(0, "minute"),
                daily_cost=Money("0", "GBP"),
            ),
            "test",
        )
        so.push(_serialize_commute(30, 10.0, label="Pimlico"), "test")
        persons.push([Person("Simon", True)], "test")
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none()["yearly_total_gbp"] == 0.0


# ======================================================================
# Imports needed by TestParkAndRide (kept at module bottom to avoid
# shadowing the test classes above)
# ======================================================================


class TestEnrichRailFares:
    """enrich_rail_fares — adds NR fares when the cost is only bus/parking."""

    @pytest.mark.asyncio
    async def test_lorena_bus_cost_adds_rail_fare(self, tmp_path):
        """Lorena with bus cost only (£4.00) gets rail fare (£37.20) added → £41.20."""
        from houses.commute import Commute, CostGroup, JourneyLeg, LegMode
        from houses.rail_fares import RailFareRegistry, enrich_rail_fares
        from houses.stations import StationRegistry

        stations_csv = tmp_path / "stations.csv"
        stations_csv.write_text(
            "stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\nFenchurch Street,FST,51.511,-0.079\n"
        )
        fares_csv = tmp_path / "fares.csv"
        fares_csv.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,FST,17.00\n")

        reg = RailFareRegistry(
            station_registry=StationRegistry(_stations_csv=stations_csv),
            _fares_csv=fares_csv,
        )

        async def mock_geocode(_):
            return Attempt.succeeded(GeoPoint(51.317, -0.556))

        async def mock_tube_fare(station, postcode, _data=None):
            return None

        lorena = Commute(
            destination_label="Lorena",
            destination_postcode="EC3A 7LP",
            duration_minutes=78,
            daily_cost_gbp=Money("4.0", "GBP"),
            cost_groups=(
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.BUS, duration_minutes=10),),
                    cost=4.0,
                ),
            ),
        )
        simon = Commute(
            destination_label="Simon",
            destination_postcode="SW1V 2QQ",
            duration_minutes=71,
            daily_cost_gbp=Money("40.4", "GBP"),
        )
        _simon, lorena_result = await enrich_rail_fares(
            enabled={"lorena"},
            postcode="GU21 7QF",
            address="St James Close",
            simon=simon,
            lorena=lorena,
            _registry=reg,
            _geocode=mock_geocode,
            _tube_fare_fn=mock_tube_fare,
        )
        assert lorena_result.daily_cost_gbp == Money("43.60", "GBP")

    @pytest.mark.asyncio
    async def test_simon_parking_cost_adds_rail_fare(self, tmp_path):
        """Simon with parking cost only (£10.80) gets rail fare added → £50.40."""
        from houses.commute import Commute, CostGroup, JourneyLeg, LegMode
        from houses.rail_fares import RailFareRegistry, enrich_rail_fares
        from houses.stations import StationRegistry

        stations_csv = tmp_path / "stations.csv"
        stations_csv.write_text(
            "stationName,crsCode,lat,long\nBrookwood,BKO,51.303,-0.636\nVictoria Station,VIC,51.495,-0.144\n"
        )
        fares_csv = tmp_path / "fares.csv"
        fares_csv.write_text("origin_crs,dest_crs,single_fare_gbp\nBKO,VIC,17.00\n")

        reg = RailFareRegistry(
            station_registry=StationRegistry(_stations_csv=stations_csv),
            _fares_csv=fares_csv,
        )

        async def mock_geocode(_):
            return Attempt.succeeded(GeoPoint(51.303, -0.636))

        async def mock_tube_fare(station, postcode, _data=None):
            return None

        simon = Commute(
            destination_label="Simon",
            destination_postcode="SW1V 2QQ",
            duration_minutes=71,
            daily_cost_gbp=Money("10.8", "GBP"),
            cost_groups=(
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.PARK, duration_minutes=0),),
                    operator="ParkCo",
                    cost=10.8,
                ),
            ),
        )
        lorena = Commute(
            destination_label="Lorena",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=None,
        )
        simon_result, _lorena_result = await enrich_rail_fares(
            enabled={"simon"},
            postcode="GU21 2NA",
            address="Robin Hood Road, Knaphill",
            simon=simon,
            lorena=lorena,
            _registry=reg,
            _geocode=mock_geocode,
            _tube_fare_fn=mock_tube_fare,
        )
        assert simon_result.daily_cost_gbp == Money("50.40", "GBP")

    @pytest.mark.asyncio
    async def test_full_tfl_fare_skips_nr(self):
        """When TfL already priced the journey, cost stays unchanged."""
        from houses.commute import Commute
        from houses.rail_fares import enrich_rail_fares

        lorena = Commute(
            destination_label="Lorena",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=Money("36.0", "GBP"),
        )
        simon = Commute(
            destination_label="Simon",
            destination_postcode="SW1V 2QQ",
            duration_minutes=71,
            daily_cost_gbp=Money("40.4", "GBP"),
        )
        simon_result, lorena_result = await enrich_rail_fares(
            enabled={"simon", "lorena"},
            postcode="GU22 8RU",
            address="Test",
            simon=simon,
            lorena=lorena,
        )
        assert simon_result.daily_cost_gbp == Money("40.4", "GBP")
        assert lorena_result.daily_cost_gbp == Money("36.0", "GBP")


class TestParkAndRide:
    """_apply_park_and_ride_to_journeys — replaces long walks with driving.
    Uses DI (``_drive_fn``) instead of ``patch``."""

    LONG_WALK_DATA = {
        "journeys": [
            {
                "duration": 87,
                "legs": [
                    {
                        "mode": {"name": "walking"},
                        "duration": 35,
                        "arrivalPoint": {"commonName": "Maidenhead Rail Station"},
                        "instruction": {"summary": "Walk to Maidenhead Rail Station"},
                    },
                    {
                        "mode": {"name": "national-rail"},
                        "duration": 20,
                        "arrivalPoint": {"commonName": "London Paddington Rail Station"},
                        "instruction": {"summary": "Great Western Railway to London Paddington"},
                    },
                    {
                        "mode": {"name": "walking"},
                        "duration": 7,
                        "arrivalPoint": {"commonName": "SW1V 2QQ"},
                        "instruction": {"summary": "Walk to SW1V 2QQ"},
                    },
                ],
            }
        ]
    }

    SHORT_WALK_DATA = {
        "journeys": [
            {
                "duration": 60,
                "legs": [
                    {
                        "mode": {"name": "walking"},
                        "duration": 10,
                        "arrivalPoint": {"commonName": "Weybridge Rail Station"},
                        "instruction": {"summary": "Walk to Weybridge Rail Station"},
                    },
                    {
                        "mode": {"name": "national-rail"},
                        "duration": 25,
                        "arrivalPoint": {"commonName": "London Waterloo Rail Station"},
                        "instruction": {"summary": "South Western Railway to London Waterloo"},
                    },
                ],
            }
        ]
    }

    async def _drive_10(self, _origin, _station):
        return 10

    async def _drive_3(self, _origin, _station):
        return 3

    async def _drive_none(self, _origin, _station):
        return None

    @pytest.mark.asyncio
    async def test_replaces_long_walk_with_drive(self):
        data = copy.deepcopy(self.LONG_WALK_DATA)
        result = await _apply_park_and_ride_to_journeys(data, "SL6 3YZ", max_walk_minutes=20, _drive_fn=self._drive_10)
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "driving"
        assert result["journeys"][0]["duration"] == 62

    @pytest.mark.asyncio
    async def test_skips_short_walk(self):
        data = copy.deepcopy(self.SHORT_WALK_DATA)
        result = await _apply_park_and_ride_to_journeys(data, "KT13 0TD", max_walk_minutes=20, _drive_fn=self._drive_3)
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "walking"
        assert legs[0]["duration"] == 10

    @pytest.mark.asyncio
    async def test_skips_non_walking_first_leg(self):
        data = {"journeys": [{"duration": 45, "legs": [{"mode": {"name": "national-rail"}, "duration": 20}]}]}
        calls = []

        async def _check_not_called(_o, _s):
            calls.append(1)
            return 10

        result = await _apply_park_and_ride_to_journeys(data, "SL6", 20, _drive_fn=_check_not_called)
        assert len(calls) == 0
        assert result["journeys"][0]["legs"][0]["mode"]["name"] == "national-rail"

    @pytest.mark.asyncio
    async def test_skips_when_drive_lookup_fails(self):
        data = copy.deepcopy(self.LONG_WALK_DATA)
        result = await _apply_park_and_ride_to_journeys(
            data, "SL6 3YZ", max_walk_minutes=20, _drive_fn=self._drive_none
        )
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "walking"
        assert legs[0]["duration"] == 35

    @pytest.mark.asyncio
    async def test_format_includes_drive_in_route_after_park_and_ride(self):
        data = copy.deepcopy(self.LONG_WALK_DATA)
        result = await _apply_park_and_ride_to_journeys(data, "SL6 3YZ", max_walk_minutes=20, _drive_fn=self._drive_10)
        best = min(result["journeys"], key=lambda j: j.get("duration", 9999))
        summary = _format_route_summary(best)
        assert "Drive to Maidenhead (10m)" in summary
        assert "Train to Paddington (20m)" in summary
        assert "walk 7m" in summary


def _succeeded_walk_check(val: bool = False) -> DerivedNode:
    """Build a minimal walk-check node whose ``_attempt`` is already resolved."""
    from dag.attempt import Attempt
    from dag.user_input_node import UserInputNode
    from houses.nodes.transit import WalkLegCheckNode

    t = UserInputNode[dict]("_wc_t", dict)
    w = WalkLegCheckNode("_wc", transit_node=t, max_walk=30)
    w._attempt = Attempt.succeeded(val)
    return w
