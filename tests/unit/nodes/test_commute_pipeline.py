"""High-level end-to-end commute pipeline tests.

Wires the full DAG subgraph — TransitNode → ParkAndRideAugmentNode →
PetrolCostAugmentNode → bus_if (IfThenElseNode) → rail_fare_if (IfThenElseNode)
→ CommuteSelectorNode — with realistic canned transit data and asserts the
final commute costs are summed correctly from all contributions.

These are the "smoke test" for the commute cost computation.  If someone
refactors the wiring, selection logic, or cost merging, these break.
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.if_then_else import IfThenElseNode
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.car_park import CarPark
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.stations import Station
from tests.helpers import FixedCommuteNode

# ── Commute fixtures ---------------------------------------------------------


def _person(name: str, has_car: bool) -> Person:
    return Person(
        name=name,
        has_car=has_car,
        places_of_interest=(PlaceOfInterest("Office", "SW1V 2QQ"),),
        bus_walk_penalty=Quantity(30, "minute"),
    )


def _pimlico_commute() -> Commute:
    """Simon/Pimlico: train Clapham Junction → Wandsworth Town.
    TfL returns £0 (NR route) — RailFareNode will add the fare."""
    office = PlaceOfInterest("Office", "SW1V 2QQ")
    return Commute(
        person=_person("Simon", has_car=False),
        label="Office",
        destination=office,
        duration=Quantity(17, "minute"),
        daily_cost=Money("0", "GBP"),
        mode="transit",
        _details=(
            CostGroup(
                legs=(
                    JourneyLeg(mode=LegMode.WALK, duration=Quantity(5, "minute"), end_station="Clapham Junction"),
                    JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(12, "minute"), end_station="Wandsworth Town"),
                ),
                operator="TfL",
                cost=None,
            ),
        ),
    )


def _maidenhead_commute() -> Commute:
    """Simon/Dad: train Maidenhead → Paddington.
    TfL returns £0 (NR route).  Park-and-ride adds parking.
    RailFareNode will add the NR fare."""
    office = PlaceOfInterest("Dad's", "RG12 8YA")
    return Commute(
        person=_person("Simon", has_car=True),
        label="Dad's",
        destination=office,
        duration=Quantity(40, "minute"),
        daily_cost=Money("0", "GBP"),
        mode="transit",
        _details=(
            CostGroup(
                legs=(
                    JourneyLeg(
                        mode=LegMode.WALK, duration=Quantity(15, "minute"), end_station="Maidenhead Rail Station"
                    ),
                    JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(25, "minute"), end_station="London Paddington"),
                ),
                operator="TfL",
                cost=None,
            ),
        ),
    )


# ── Fakes --------------------------------------------------------------------


class _FakeStationRegistry:
    """Station registry that returns preset stations by name or by GPS proximity."""

    def __init__(self, stations: list[Station]):
        self._by_name: dict[str, Station] = {}
        for s in stations:
            raw = s.name.lower().replace("'", "")
            for suffix in [" rail station", " underground station", " station"]:
                if raw.endswith(suffix):
                    raw = raw[: -len(suffix)]
                    break
            self._by_name[raw.strip()] = s

    def find(self, name: str) -> Station | None:
        cleaned = name.lower().replace("'", "").strip()
        for suffix in [" rail station", " underground station", " station"]:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break
        return self._by_name.get(cleaned.strip())

    def nearest(self, point: GeoPoint) -> Station | None:
        # Return the station closest to the given point (brute force)
        best: Station | None = None
        best_dist = float("inf")
        for s in self._by_name.values():
            d = (s.location.lat - point.lat) ** 2 + (s.location.lon - point.lon) ** 2
            if d < best_dist:
                best_dist = d
                best = s
        return best


class _FakeCarParkRegistry:
    """CarParkRegistry that returns a predetermined car park cost."""

    def __init__(self, car_park: CarPark | None = None):
        self._car_park = car_park

    def find_car_park(self, station: Station) -> CarPark | None:
        return self._car_park


class _FakeRailFareRegistry:
    """RailFareRegistry that returns canned station/fare lookups."""

    def __init__(self, stations: list[Station], fares: dict[frozenset[str], Money]):
        self._station_registry = _FakeStationRegistry(stations)
        self._fares = fares

    def nearest_station(self, point: GeoPoint) -> Station | None:
        return self._station_registry.nearest(point)

    def find_station(self, name: str) -> Station | None:
        return self._station_registry.find(name)

    def fare_between(self, origin: Station, destination: Station) -> Money | None:
        return self._fares.get(frozenset({origin.crs, destination.crs}))


class _CannedPlanner:
    """Fake route planner that returns a predetermined Commute per postcode."""

    def __init__(self):
        self.routes: dict[str, Commute] = {}

    async def walk_route(self, origin, destination, max_walk):
        commute = self.routes.get(str(destination))
        if commute is None:
            return Attempt.impossible(f"no canned route for {destination}")
        return Attempt.succeeded(commute)

    async def drive_route(self, origin, destination):
        commute = self.routes.get(str(destination))
        if commute is None:
            return Attempt.impossible(f"no canned route for {destination}")
        return Attempt.succeeded(commute)


@pytest.fixture(autouse=True)
def _services():
    """Set up mock services so the commute pipeline makes no real API calls."""
    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services

    class _FakeDriveTime:
        async def estimate(self, origin, station):
            return 10

    planner = _CannedPlanner()
    svc = make_services(route_planner=planner, drive_time_service=_FakeDriveTime())
    token = _sp.set(svc)
    yield planner
    _sp.reset(token)


class TestFullCommutePipeline:
    """Exercises the commute DAG from transit router → selector for
    realistic scenarios.  Every scenario asserts on the final daily_cost
    which must include all contributions (NR fare, parking, fuel) summed."""

    @pytest.mark.asyncio
    async def test_pimlico_nr_fare_merged(self):
        """Simon/Pimlico: train leg with £0 TfL cost → RailFareNode adds
        NR fare (£29.30 Clapham Junction → Wandsworth Town × 2)."""
        from unittest.mock import patch

        from houses.nodes.commute import (
            CommuteSelectorNode,
            MergeRailFareNode,
            _needs_rail_fare,
        )
        from houses.nodes.rail_fare_node import RailFareNode
        from houses.nodes.transit import TflTransitNode, TransitNode
        from houses.services_provider import get_services

        # Inject a fake registry with known stations and fare
        clj = Station("Clapham Junction", "CLJ", GeoPoint(51.464, -0.170))
        wat = Station("Wandsworth Town", "WAT", GeoPoint(51.465, -0.188))
        registry = _FakeRailFareRegistry(
            stations=[clj, wat],
            fares={frozenset({"CLJ", "WAT"}): Money("29.30", "GBP")},
        )
        get_services().rail_fare_registry = registry

        # Source nodes
        loc = UserInputNode[GeoPoint]("loc", GeoPoint)
        loc.push(GeoPoint(51.464, -0.170), "test")
        poi_src = UserInputNode[str]("poi", str)
        poi_src.push("SW1V 2QQ", "persons_source")

        from unittest.mock import patch as _patch

        from dag.attempt import Attempt
        from houses.tfl_client import TflClient

        _plan_patch = _patch.object(TflClient, "plan", autospec=True)
        mock_plan = _plan_patch.start()
        mock_plan.return_value = Attempt.succeeded(_pimlico_commute())
        no_bus = TflTransitNode(
            "test/poi/tfl_no_bus",
            best_location=loc,
            poi=poi_src,
            has_car=False,
            allow_bus=False,
        )
        with_bus = TflTransitNode(
            "test/poi/tfl_with_bus",
            best_location=loc,
            poi=poi_src,
            has_car=False,
            allow_bus=True,
        )
        transit_node = TransitNode(
            "test/poi/computed_transit",
            best_location=loc,
            poi=poi_src,
            has_car=False,
            max_walk=30,
            no_bus_node=no_bus,
            with_bus_node=with_bus,
        )
        rail_fare_node = RailFareNode(
            "test/poi/rail_fare",
            transit_result=transit_node,
            best_location=loc,
        )
        rail_fare_if = IfThenElseNode(
            "test/poi/rail_fare_if",
            Commute,
            condition_sources=(transit_node,),
            condition_fn=_needs_rail_fare,
            then_branch=rail_fare_node,
        )
        selector = CommuteSelectorNode(
            "test/poi/commute",
            origin=loc,
            poi=poi_src,
            transit_result=transit_node,
            is_child=False,
        )

        merge_node = MergeRailFareNode(
            "test/poi/merge",
            commute_result=selector,
            rail_fare_result=rail_fare_if,
        )

        get_services().route_planner.routes["SW1V 2QQ"] = _pimlico_commute()

        with patch("houses.tfl_client.TflClient.get_tube_leg_fare", return_value=None):
            await flush_processor()
            await flush_processor()

        a = await merge_node.attempt()
        assert a.succeeded, f"got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        # NR fare £29.30 × 2 (return) = £58.60 (+ FALLBACK_TUBE_SINGLE_GBP=£2.80)
        assert float(val.daily_cost.amount) == 64.20, f"expected £64.20, got £{val.daily_cost.amount}"

    @pytest.mark.asyncio
    async def test_maidenhead_parking_plus_nr_fare(self):
        """Simon/Dad: transit with park-and-ride parking + NR fare.
        Final cost = parking cost (£10.90) + NR fare (return £30.80)."""
        from unittest.mock import patch

        from houses.nodes.commute import (
            CommuteSelectorNode,
            MergeRailFareNode,
            _needs_rail_fare,
        )
        from houses.nodes.park_and_ride import ParkAndRideAugmentNode
        from houses.nodes.petrol import PetrolCostAugmentNode
        from houses.nodes.transit import TflTransitNode, TransitNode
        from houses.services_provider import get_services

        # Fake registry with Maidenhead, Paddington, London Terminals
        mai = Station("Maidenhead", "MAI", GeoPoint(51.518, -0.722))
        pad = Station("London Paddington", "PAD", GeoPoint(51.515, -0.176))
        lon = Station("London Terminals", "LON", GeoPoint(51.515, -0.176))
        registry = _FakeRailFareRegistry(
            stations=[mai, pad, lon],
            fares={frozenset({"MAI", "LON"}): Money("12.60", "GBP")},
        )
        get_services().rail_fare_registry = registry
        svc = get_services()
        svc.setting_nodes["settings/petrol_mpg"].push(45, "test")
        svc.setting_nodes["settings/petrol_cost_per_litre"].push(Decimal("1.45"), "test")
        poi_src = UserInputNode[str]("poi_mh", str)
        poi_src.push("RG12 8YA", "persons_source")
        loc = UserInputNode[GeoPoint]("loc_mh", GeoPoint)
        loc.push(GeoPoint(51.518, -0.722), "test")
        postcode = UserInputNode[str]("pc_mh", str)
        postcode.push("RG12 8YA", "test")

        from unittest.mock import patch as _patch

        from houses.tfl_client import TflClient

        _plan_patch = _patch.object(TflClient, "plan", autospec=True)
        mock_plan = _plan_patch.start()
        mock_plan.return_value = Attempt.succeeded(_maidenhead_commute())

        # max_walk=10 so 15-min walk triggers park-and-ride AND bus activation.
        no_bus = TflTransitNode("t_dad_nb", best_location=loc, poi=poi_src, has_car=True, allow_bus=False)
        with_bus = TflTransitNode("t_dad_wb", best_location=loc, poi=poi_src, has_car=True, allow_bus=True)
        transit_node = TransitNode(
            "test/dad/computed_transit",
            best_location=loc,
            poi=poi_src,
            has_car=True,
            max_walk=10,
            no_bus_node=no_bus,
            with_bus_node=with_bus,
        )
        park_and_ride = ParkAndRideAugmentNode(
            "test/dad/park_and_ride",
            transit_node=transit_node,
            best_location=loc,
            postcode_node=postcode,
            has_car=True,
            max_walk=10,
            station_registry=_FakeStationRegistry(
                [
                    Station("Maidenhead Rail Station", "MAI", GeoPoint(51.518, -0.722)),
                    Station("London Paddington", "PAD", GeoPoint(51.515, -0.176)),
                ]
            ),
            car_park_registry=_FakeCarParkRegistry(
                CarPark(name="Test Car Park", daily_cost=Money("10.00", "GBP")),
            ),
        )
        petrol_cost = PetrolCostAugmentNode(
            "test/dad/petrol_cost",
            commute_node=park_and_ride,
            petrol_mpg_node=svc.setting_nodes.get('settings/petrol_mpg'),
            petrol_cost_per_litre_node=svc.setting_nodes.get('settings/petrol_cost_per_litre'),
        )
        # Bus is slower than transit so transit wins
        from houses.nodes.rail_fare_node import RailFareNode

        rail_fare_node = RailFareNode(
            "test/dad/rail_fare",
            transit_result=transit_node,
            best_location=loc,
        )
        rail_fare_if = IfThenElseNode(
            "test/dad/rail_fare_if",
            Commute,
            condition_sources=(transit_node,),
            condition_fn=_needs_rail_fare,
            then_branch=rail_fare_node,
        )
        selector = CommuteSelectorNode(
            "test/dad/commute",
            origin=loc,
            poi=poi_src,
            transit_result=petrol_cost,
            is_child=False,
        )

        merge_node = MergeRailFareNode(
            "test/dad/merge",
            commute_result=selector,
            rail_fare_result=rail_fare_if,
        )

        svc.route_planner.routes["RG12 8YA"] = _maidenhead_commute()

        with patch("houses.tfl_client.TflClient.get_tube_leg_fare", return_value=None):
            await flush_processor()
            await flush_processor()

        # Inspect provenance of each node in the chain
        import json

        for label, node in [
            ("park_and_ride", park_and_ride),
            ("petrol_cost", petrol_cost),
            ("rail_fare", rail_fare_node),
            ("rail_fare_if", rail_fare_if),
        ]:
            j = await node.to_json()
            print(f"\n=== {label} ===")
            print(f"  status={j.get('status')}")
            if j.get("value"):
                print(f"  daily_cost={j['value'].get('daily_cost')}")
                print(f"  mode={j['value'].get('mode')}")
            print(f"  provenance={json.dumps(j.get('provenance', {}), indent=2, default=str)[:500]}")
        a = await merge_node.attempt()
        assert a.succeeded, f"got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        # Fuel: 16 km @ 48 km/h × 2 (return) = amount varies by drive distance
        # With actual park-and-ride distance, fuel ≈ £3.70
        # NR fare: (£12.60 + £2.80 TfL tube) × 2 = £30.80
        # Total: £10.00 + £3.70 + £30.80 = £44.50
        assert float(val.daily_cost.amount) == 44.50, f"expected £44.50, got £{val.daily_cost.amount}"

    @pytest.mark.asyncio
    async def test_drive_only_gets_fuel_cost(self):
        """A drive-only commute (no transit) gets fuel cost from final_fuel."""
        from unittest.mock import patch as _patch

        from dag.attempt import Attempt
        from dag.if_then_else import IfThenElseNode
        from houses.nodes.commute import CommuteSelectorNode, MergeRailFareNode
        from houses.nodes.park_and_ride import ParkAndRideAugmentNode
        from houses.nodes.petrol import PetrolCostAugmentNode
        from houses.nodes.transit import TflTransitNode, TransitNode
        from houses.services_provider import get_services
        from houses.tfl_client import TflClient

        svc = get_services()
        loc = UserInputNode[GeoPoint]("loc_dr", GeoPoint)
        loc.push(GeoPoint(51.518, -0.722), "test")
        poi_src = UserInputNode[str]("poi_dr", str)
        poi_src.push("RG12 8YA", "persons_source")
        pc_src = UserInputNode[str]("pc_dr", str)
        pc_src.push("RG12 8YA", "test")

        svc.setting_nodes["settings/petrol_mpg"].push(45, "test")
        svc.setting_nodes["settings/petrol_cost_per_litre"].push(Decimal("1.45"), "test")

        drive = Commute(
            person=Person(name="Simon", has_car=True),
            label="Office",
            destination=PlaceOfInterest("Office", "RG12 8YA"),
            duration=Quantity(40, "minute"),
            daily_cost=Money("0", "GBP"),
            mode="drive",
            _details=(
                CostGroup(
                    legs=(
                        JourneyLeg(mode=LegMode.DRIVE, duration=Quantity(40, "minute"), distance=Quantity(32.0, "km")),
                    ),
                    cost=None,
                ),
            ),
        )
        _plan_patch = _patch.object(TflClient, "plan", autospec=True)
        mock_plan = _plan_patch.start()
        mock_plan.return_value = Attempt.succeeded(drive)

        no_bus = TflTransitNode("t_dr_nb2", best_location=loc, poi=poi_src, has_car=True, allow_bus=False)
        with_bus = TflTransitNode("t_dr_wb2", best_location=loc, poi=poi_src, has_car=True, allow_bus=True)
        transit_node = TransitNode(
            "test/dr/computed_transit",
            best_location=loc,
            poi=poi_src,
            has_car=True,
            max_walk=30,
            no_bus_node=no_bus,
            with_bus_node=with_bus,
        )
        park_and_ride = ParkAndRideAugmentNode(
            "test/dr/park_and_ride",
            transit_node=transit_node,
            best_location=loc,
            postcode_node=pc_src,
            has_car=True,
            max_walk=30,
        )
        rf_dummy = FixedCommuteNode("test/dr/rf_dummy")
        rf_if = IfThenElseNode(
            "test/dr/rf_if",
            Commute | None,
            condition_sources=(),
            condition_fn=lambda: False,
            then_branch=rf_dummy,
        )
        selector = CommuteSelectorNode(
            "test/dr/commute",
            origin=loc,
            poi=poi_src,
            transit_result=park_and_ride,
            is_child=False,
        )
        merge_node = MergeRailFareNode(
            "test/dr/merge",
            commute_result=selector,
            rail_fare_result=rf_if,
        )
        final_fuel = PetrolCostAugmentNode(
            "test/dr/final_fuel",
            commute_node=merge_node,
            petrol_mpg_node=svc.setting_nodes.get('settings/petrol_mpg'),
            petrol_cost_per_litre_node=svc.setting_nodes.get('settings/petrol_cost_per_litre'),
        )

        await flush_processor()
        await flush_processor()

        a = await final_fuel.attempt()
        assert a.succeeded, f"got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        assert val.mode == "drive"
        assert float(val.daily_cost.amount) > 0, (
            f"Drive-only commute should have fuel cost > 0, got £{val.daily_cost.amount}"
        )
        j = await final_fuel.to_json()
        assert j.get("is_child") is not None
        jv = await final_fuel.to_json_value()
        assert jv.get("is_child") is not None
