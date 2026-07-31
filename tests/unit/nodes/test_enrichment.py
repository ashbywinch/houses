from __future__ import annotations

import pytest

import dag.user_input_node  # noqa: F401 — register Money/Quantity schemas
from dag.attempt import Attempt
from dag.user_input_node import UserInputNode
from houses.model.domain import Commute, Person, PlaceOfInterest


class TestEpcNode:
    @pytest.mark.asyncio
    async def test_impossible_without_address(self):
        from houses.nodes.epc_node import EpcNode

        addr = UserInputNode[str]("addr_epc", str)
        pc = UserInputNode[str]("pc_epc", str)
        node = EpcNode("epc", best_address=addr, postcode_node=pc)
        a = await node.attempt()
        assert not a.succeeded


class TestCouncilTaxNode:
    @pytest.mark.asyncio
    async def test_impossible_without_postcode(self):
        from houses.nodes.epc_node import CouncilTaxNode

        addr = UserInputNode[str]("addr_ct", str)
        pc = UserInputNode[str]("pc_ct", str)
        node = CouncilTaxNode("ct", best_address=addr, postcode_node=pc)
        a = await node.attempt()
        assert not a.succeeded

    @pytest.mark.asyncio
    async def test_impossible_without_address(self):
        from houses.nodes.epc_node import CouncilTaxNode

        addr = UserInputNode[str]("addr_ct2", str)
        pc = UserInputNode[str]("pc_ct2", str)
        node = CouncilTaxNode("ct2", best_address=addr, postcode_node=pc)
        a = await node.attempt()
        assert not a.succeeded

    @pytest.mark.asyncio
    async def test_returns_yearly_cost_key(self):
        """CouncilTaxNode returns yearly_cost not cost (matches frontend expectation)."""
        from money import Money

        from houses.council_tax_info import CouncilTaxInfo
        from houses.nodes.epc_node import CouncilTaxNode
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        class _FakeCT:
            async def lookup(self, postcode, address=""):
                from dag.attempt import Attempt

                return Attempt.succeeded(CouncilTaxInfo(band="D", yearly_cost=Money("1800", "GBP")))

        svc = make_services(council_tax_service=_FakeCT())
        token = _sp.set(svc)
        try:
            addr = UserInputNode[str]("addr_ct3", str)
            pc = UserInputNode[str]("pc_ct3", str)
            node = CouncilTaxNode("ct3", best_address=addr, postcode_node=pc)
            addr.push("1 High Street, Egham, TW20 9JP", "test")
            pc.push("TW20 9JP", "test")

            from dag.scheduler import flush_processor

            await flush_processor()

            a = await node.attempt()
            assert a.succeeded
            val = a.value_or_none()
            assert val is not None
            assert val.band == "D"
            assert val.yearly_cost == Money("1800", "GBP")
        finally:
            _sp.reset(token)


class TestWalkabilityNode:
    @pytest.mark.asyncio
    async def test_impossible_without_location(self):
        from houses.nodes.area import WalkabilityNode

        loc = UserInputNode[dict]("loc_w", dict)
        addr = UserInputNode[str]("addr_w", str)
        node = WalkabilityNode("wlk", best_location=loc, best_address=addr)
        a = await node.attempt()
        assert not a.succeeded


class TestNearestTownNode:
    @pytest.mark.asyncio
    async def test_impossible_without_location(self):
        from houses.nodes.area import NearestTownNode

        loc = UserInputNode[dict]("loc_nt", dict)
        node = NearestTownNode("nt", best_location=loc)
        a = await node.attempt()
        assert not a.succeeded

    @pytest.mark.asyncio
    async def test_returns_town_name(self):
        from dag.scheduler import flush_processor
        from houses.geo import GeoPoint
        from houses.nodes.area import NearestTownNode

        loc = UserInputNode[GeoPoint]("loc_nt2", GeoPoint)
        node = NearestTownNode("nt2", best_location=loc)
        loc.push(GeoPoint(lat=51.5, lon=-0.1), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"nearest_town failed: {a.error}"
        assert a.value == "Test Town"


class TestTownDescNode:
    @pytest.mark.asyncio
    async def test_impossible_without_deps(self):
        from houses.nodes.area import TownDescNode

        loc = UserInputNode[dict]("loc_td2", dict)
        nearest = UserInputNode[str]("nearest_td2", str)
        addr_town = UserInputNode[str]("addr_td2", str)
        pc = UserInputNode[str]("pc_td2", str)
        node = TownDescNode("td2", best_location=loc, nearest_town=nearest, town_name=addr_town, postcode_node=pc)
        a = await node.attempt()
        assert not a.succeeded

    @pytest.mark.asyncio
    async def test_prefers_address_town_over_nearest(self):
        from dag.scheduler import flush_processor
        from houses.geo import GeoPoint
        from houses.nodes.area import TownDescNode
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        seen_town = None
        seen_pc = None

        class _RecordingTownDesc:
            async def describe(self, town_name: str, postcode: str) -> Attempt[str]:
                nonlocal seen_town, seen_pc
                seen_town = town_name
                seen_pc = postcode
                return Attempt.succeeded("A leafy town.")

        token = _sp.set(make_services(town_desc_service=_RecordingTownDesc()))
        try:
            loc = UserInputNode[GeoPoint]("loc_td3", GeoPoint)
            nearest = UserInputNode[str]("nearest_td3", str)
            addr_town = UserInputNode[str]("addr_td3", str)
            pc = UserInputNode[str]("pc_td3", str)
            node = TownDescNode("td3", best_location=loc, nearest_town=nearest, town_name=addr_town, postcode_node=pc)
            loc.push(GeoPoint(lat=51.5, lon=-0.1), "test")
            nearest.push("London", "test")
            addr_town.push("Southall", "test")
            pc.push("UB2 4GN", "test")
            await flush_processor()
            a = await node.attempt()
            assert a.succeeded, f"town_desc failed: {a.error}"
            # Must prefer address-extracted "Southall" over reverse-geocoded "London"
            assert seen_town == "Southall", f"Expected address town, got {seen_town}"
            assert seen_pc == "UB2 4GN"
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_falls_back_to_nearest_when_address_has_no_town(self):
        from dag.scheduler import flush_processor
        from houses.geo import GeoPoint
        from houses.nodes.area import TownDescNode
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        seen_town = None

        class _RecordingTownDesc:
            async def describe(self, town_name: str, postcode: str) -> Attempt[str]:
                nonlocal seen_town
                seen_town = town_name
                return Attempt.succeeded("A leafy town.")

        token = _sp.set(make_services(town_desc_service=_RecordingTownDesc()))
        try:
            loc = UserInputNode[GeoPoint]("loc_td4", GeoPoint)
            nearest = UserInputNode[str]("nearest_td4", str)
            addr_town = UserInputNode[str]("addr_td4", str)
            pc = UserInputNode[str]("pc_td4", str)
            node = TownDescNode("td4", best_location=loc, nearest_town=nearest, town_name=addr_town, postcode_node=pc)
            loc.push(GeoPoint(lat=51.5, lon=-0.1), "test")
            nearest.push("Pangbourne", "test")
            addr_town.push("", "test")  # empty string = no town found in address
            pc.push("RG8 7AS", "test")
            await flush_processor()
            a = await node.attempt()
            assert a.succeeded, f"town_desc failed: {a.error}"
            assert seen_town == "Pangbourne", f"Expected nearest town fallback, got {seen_town}"
        finally:
            _sp.reset(token)


class TestGeocodeNode:
    @pytest.mark.asyncio
    async def test_impossible_without_address(self):
        from houses.nodes.geocode import GeocodeNode

        addr = UserInputNode[str]("addr_gc", str)
        node = GeocodeNode("gc", best_address=addr)
        a = await node.attempt()
        assert not a.succeeded


class TestParkAndRideAugmentNode:
    @pytest.mark.asyncio
    async def test_impossible_without_transit(self):
        from dag.user_input_node import UserInputNode
        from houses.geo import GeoPoint
        from houses.nodes.park_and_ride import ParkAndRideAugmentNode

        transit = UserInputNode[Commute]("t_pr3", Commute)
        loc = UserInputNode[GeoPoint]("loc_pr3", GeoPoint)
        pc = UserInputNode[str]("pc_pr3", str)
        node = ParkAndRideAugmentNode(
            "pr3", transit_node=transit, best_location=loc, postcode_node=pc, has_car=True, max_walk=20
        )
        a = await node.attempt()
        assert not a.succeeded

    @staticmethod
    def _walk_commute(walk_min: int, end_station: str = "Maidenhead Rail Station", cost: str = "12.50") -> Commute:
        from money import Money
        from pint import Quantity

        from houses.commute import CostGroup, JourneyLeg, LegMode

        walk_leg = JourneyLeg(mode=LegMode.WALK, duration=Quantity(walk_min, "minute"), end_station=end_station)
        train_leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(20, "minute"), end_station="London Paddington")
        group = CostGroup(legs=(walk_leg, train_leg), operator="", cost=None)
        return Commute(
            person=Person(name="Simon", has_car=True),
            label="Office",
            destination=PlaceOfInterest("Office", "SW1V 2QQ"),
            duration=Quantity(55 if walk_min <= 55 else 55 + walk_min - 35, "minute"),
            daily_cost=Money(cost, "GBP"),
            mode="transit",
            _details=(group,),
        )

    @staticmethod
    def _make_node(
        transit: UserInputNode[Commute],
        loc: UserInputNode,
        has_car: bool = True,
        max_walk: int = 20,
        pc_node: UserInputNode | None = None,
    ):
        if pc_node is None:
            pc_node = UserInputNode[str]("pc_dummy", str)
            pc_node.push("SW1V 2QQ", "test")
        from houses.nodes.park_and_ride import ParkAndRideAugmentNode

        return ParkAndRideAugmentNode(
            "pr_test",
            transit_node=transit,
            best_location=loc,
            postcode_node=pc_node,
            has_car=has_car,
            max_walk=max_walk,
        )

    @pytest.mark.asyncio
    async def test_replaces_long_walk_with_drive(self):
        """35min walk, max_walk=20 — walk replaced with drive, parking added."""
        from money import Money

        from dag.scheduler import flush_processor
        from houses.geo import GeoPoint
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        # Mock the drive time service
        class _FakeDriveTime:
            async def estimate(self, origin, station):
                return 10  # 10 min drive

        svc = make_services(drive_time_service=_FakeDriveTime())
        token = _sp.set(svc)
        try:
            transit = UserInputNode[Commute]("pr_lw", Commute)
            loc = UserInputNode[GeoPoint]("loc_lw", GeoPoint)
            commute = self._walk_commute(35)
            transit.push(commute, "TfL")
            loc.push(GeoPoint(51.5, -0.1), "user")
            node = self._make_node(transit, loc)
            await flush_processor()

            a = await node.attempt()
            assert a.succeeded, f"Expected succeeded, got {a.error}"
            val = a.value_or_none()
            assert val is not None
            # First CG: drive leg (replaced walk)
            assert val.details[0].legs[0].mode.name == "DRIVE"
            assert int(val.details[0].legs[0].duration.magnitude) == 10
            # Second CG: parking
            assert val.details[1].legs[0].mode.name == "PARK"
            # Third CG: remaining transit legs (train from original first CG)
            assert val.details[2].legs[0].mode.name == "TRAIN"
            # daily_cost = original cost (£12.50) + parking (£9.00) = £21.50
            expected_cost = Money("21.50", "GBP")
            assert float(val.daily_cost.amount) == float(expected_cost.amount)
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_preserves_transit_cost_group_when_walk_is_separate(self):
        """Realistic _build_cost_groups shape: walk in CG0, transit in CG1.
        ParkAndRideAugmentNode must preserve CG1 so duration sums all legs."""
        from money import Money
        from pint import Quantity

        from dag.scheduler import flush_processor
        from houses.car_park import CarPark, CarParkRegistry
        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.geo import GeoPoint
        from houses.nodes.park_and_ride import ParkAndRideAugmentNode
        from houses.services_provider import _request_services as _sp
        from houses.stations import StationRegistry
        from tests.helpers import make_services

        class _FakeDriveTime:
            async def estimate(self, origin, station):
                return 12

        class _FakeStation:
            name = "Woking Rail Station"
            crs = "WOK"

        class _FakeStationRegistry(StationRegistry):
            def find(self, name):
                return _FakeStation() if "Woking" in name else None

        class _FakeCarParkRegistry(CarParkRegistry):
            def find_car_park(self, station):
                return CarPark(name="Woking Park", daily_cost=Money("12.80", "GBP"))

        svc = make_services(drive_time_service=_FakeDriveTime())
        token = _sp.set(svc)
        try:
            transit = UserInputNode[Commute]("pr_mcg", Commute)
            loc = UserInputNode[GeoPoint]("loc_mcg", GeoPoint)
            # Production _build_cost_groups shape: walk before transit in its own CG
            walk_cg = CostGroup(
                legs=(
                    JourneyLeg(mode=LegMode.WALK, duration=Quantity(39, "minute"), end_station="Woking Rail Station"),
                ),
                cost=None,
                operator="",
            )
            transit_cg = CostGroup(
                legs=(
                    JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(26, "minute")),
                    JourneyLeg(mode=LegMode.TUBE, duration=Quantity(8, "minute")),
                    JourneyLeg(mode=LegMode.TUBE, duration=Quantity(3, "minute")),
                    JourneyLeg(mode=LegMode.WALK, duration=Quantity(7, "minute")),
                ),
                cost=Money("12.50", "GBP"),
                operator="TfL",
            )
            commute = Commute(
                person=Person(name="", has_car=True, is_child=False),
                label="Office",
                destination=PlaceOfInterest("Office", "SW1V 2QQ"),
                duration=Quantity(94, "minute"),
                daily_cost=Money("0", "GBP"),
                mode="transit",
                _details=(walk_cg, transit_cg),
            )
            transit.push(commute, "TfL")
            loc.push(GeoPoint(51.5, -0.1), "user")
            pc_node = UserInputNode[str]("pc_mcg", str)
            pc_node.push("GU21 7QF", "test")

            node = ParkAndRideAugmentNode(
                "pr_mcg",
                transit_node=transit,
                best_location=loc,
                postcode_node=pc_node,
                has_car=True,
                max_walk=20,
                station_registry=_FakeStationRegistry(),
                car_park_registry=_FakeCarParkRegistry(),
            )
            await flush_processor()

            a = await node.attempt()
            assert a.succeeded, f"Expected succeeded, got {a.error}"
            val = a.value_or_none()
            assert val is not None

            # Must have 3 cost groups: drive (replaced walk), park, original transit
            assert len(val.details) == 3, f"Expected 3 CGs, got {len(val.details)}"
            assert val.details[0].legs[0].mode.name == "DRIVE"
            assert val.details[1].legs[0].mode.name == "PARK"
            assert val.details[2].legs[0].mode.name == "TRAIN"

            # Duration must sum ALL legs, not just the drive leg
            total_legs = sum(int(leg.duration.magnitude) for cg in val.details for leg in cg.legs)
            assert int(val.duration.magnitude) == total_legs, (
                f"duration {int(val.duration.magnitude)} != sum of legs {total_legs}"
            )
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_skips_short_walk(self):
        """10min walk, max_walk=20 — walk stays as walk, no parking."""
        from dag.scheduler import flush_processor
        from houses.geo import GeoPoint

        transit = UserInputNode[Commute]("pr_sw", Commute)
        loc = UserInputNode[GeoPoint]("loc_sw", GeoPoint)
        commute = self._walk_commute(10)
        transit.push(commute, "TfL")
        loc.push(GeoPoint(51.5, -0.1), "user")
        node = self._make_node(transit, loc)
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        # Walk leg unchanged
        assert val.details[0].legs[0].mode.name == "WALK"
        assert int(val.details[0].legs[0].duration.magnitude) == 10
        # No parking group
        assert len(val.details) == 1

    @pytest.mark.asyncio
    async def test_skips_non_walking_first_leg(self):
        """First leg is train — no change."""
        from money import Money
        from pint import Quantity

        from dag.scheduler import flush_processor
        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.geo import GeoPoint

        train_leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(20, "minute"), end_station="London Paddington")
        walk_leg = JourneyLeg(mode=LegMode.WALK, duration=Quantity(5, "minute"), end_station="Platform 1")
        group = CostGroup(legs=(train_leg, walk_leg), operator="", cost=None)
        commute = Commute(
            person=Person(name="Simon", has_car=True),
            label="Office",
            destination=PlaceOfInterest("Office", "SW1V 2QQ"),
            duration=Quantity(25, "minute"),
            daily_cost=Money("12.50", "GBP"),
            mode="transit",
            _details=(group,),
        )
        transit = UserInputNode[Commute]("pr_nw", Commute)
        loc = UserInputNode[GeoPoint]("loc_nw", GeoPoint)
        transit.push(commute, "TfL")
        loc.push(GeoPoint(51.5, -0.1), "user")
        node = self._make_node(transit, loc)
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        # First leg is still TRAIN
        assert val.details[0].legs[0].mode.name == "TRAIN"
        assert len(val.details) == 1

    @pytest.mark.asyncio
    async def test_skips_when_drive_lookup_fails(self):
        """Drive lookup fails (returns None) — walk stays as walk."""
        from dag.scheduler import flush_processor
        from houses.geo import GeoPoint
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        class _FakeDriveTimeFail:
            async def estimate(self, origin, station):
                return None  # drive lookup fails

        svc = make_services(drive_time_service=_FakeDriveTimeFail())
        token = _sp.set(svc)
        try:
            transit = UserInputNode[Commute]("pr_ns2", Commute)
            loc = UserInputNode[GeoPoint]("loc_ns2", GeoPoint)
            commute = self._walk_commute(35, end_station="Nowhere Station")
            transit.push(commute, "TfL")
            loc.push(GeoPoint(51.5, -0.1), "user")
            node = self._make_node(transit, loc)
            await flush_processor()

            a = await node.attempt()
            assert a.succeeded
            val = a.value_or_none()
            assert val is not None
            assert val.details[0].legs[0].mode.name == "WALK"
            assert int(val.details[0].legs[0].duration.magnitude) == 35
            assert len(val.details) == 1
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_format_includes_drive_in_route_after_park_and_ride(self):
        """Provenance description mentions parking after park-and-ride."""
        from dag.scheduler import flush_processor
        from houses.geo import GeoPoint
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        class _FakeDriveTime:
            async def estimate(self, origin, station):
                return 8  # 8 min drive

        svc = make_services(drive_time_service=_FakeDriveTime())
        token = _sp.set(svc)
        try:
            transit = UserInputNode[Commute]("pr_fmt2", Commute)
            loc = UserInputNode[GeoPoint]("loc_fmt2", GeoPoint)
            commute = self._walk_commute(35)
            transit.push(commute, "TfL")
            loc.push(GeoPoint(51.5, -0.1), "user")
            node = self._make_node(transit, loc)
            await flush_processor()

            a = await node.attempt()
            assert a.succeeded
            prov = await node.build_provenance()
            assert prov.description
            assert "parking" in prov.description.lower()
        finally:
            _sp.reset(token)


class TestTransitCostAttribution:
    """Park-and-ride should attribute costs to transit legs, not just parking."""

    @pytest.mark.asyncio
    async def test_transit_leg_has_cost_after_park_and_ride(self):
        from money import Money
        from pint import Quantity

        from dag.user_input_node import UserInputNode
        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.geo import GeoPoint
        from houses.nodes.park_and_ride import ParkAndRideAugmentNode
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        class _FakeDriveTime:
            async def estimate(self, origin, station):
                return 10

        svc = make_services(drive_time_service=_FakeDriveTime())
        token = _sp.set(svc)
        try:
            transit = UserInputNode[Commute]("t_cta_transit", Commute)
            loc = UserInputNode[GeoPoint]("t_cta_loc", GeoPoint)
            pc = UserInputNode[str]("t_cta_pc", str)
            pc.push("SL6 3YZ", "test")
            loc.push(GeoPoint(51.5, -0.1), "test")

            walk_leg = JourneyLeg(
                mode=LegMode.WALK, duration=Quantity(30, "minute"), end_station="Maidenhead Rail Station"
            )
            train_leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(30, "minute"), end_station="London Paddington")
            group = CostGroup(legs=(walk_leg, train_leg), operator="TfL", cost=None)
            c = Commute(
                person=Person(name="Simon", has_car=True),
                label="Office",
                destination=PlaceOfInterest("Office", "SW1V 2QQ"),
                duration=Quantity(60, "minute"),
                daily_cost=Money("12.50", "GBP"),
                mode="transit",
                _details=(group,),
            )
            transit.push(c, "test")

            node = ParkAndRideAugmentNode(
                "t_cta_pr",
                transit_node=transit,
                best_location=loc,
                postcode_node=pc,
                has_car=True,
                max_walk=20,
            )

            # Call refresh directly — no global queue dependency
            await node.refresh()

            a = await node.attempt()
            assert a.succeeded

            result = a.value_or_none()
            assert result.daily_cost is not None
            assert float(result.daily_cost.amount) == 21.50, f"Expected 21.50, got {result.daily_cost}"

            train_groups = [cg for cg in result.details if any(leg.mode == LegMode.TRAIN for leg in cg.legs)]
            assert len(train_groups) > 0

            train_group = train_groups[0]
            assert train_group.cost is not None, (
                f"Train CostGroup has no cost. "
                f"total={result.daily_cost}, "
                f"groups={[(cg.cost, [str(leg.mode) for leg in cg.legs]) for cg in result.details]}"
            )
            c = float(train_group.cost.amount)
            assert c == 12.50, f"Expected £12.50 on train group, got {train_group.cost}"
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_park_and_ride_attributes_cost_when_tfl_returns_zero(self):
        """Park-and-ride with existing_cost=0 (TfL NR case): train CostGroup
        must get cost attributed (even if 0) so the frontend shows it and
        CommuteSelectorNode can apply the NR fare."""
        from money import Money
        from pint import Quantity

        from dag.user_input_node import UserInputNode
        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.geo import GeoPoint
        from houses.nodes.park_and_ride import ParkAndRideAugmentNode
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        class _FakeDriveTime:
            async def estimate(self, origin, station):
                return 10

        svc = make_services(drive_time_service=_FakeDriveTime())
        token = _sp.set(svc)
        try:
            transit = UserInputNode[Commute]("t_cta_zero", Commute)
            loc = UserInputNode[GeoPoint]("t_cta_zero_loc", GeoPoint)
            pc = UserInputNode[str]("t_cta_zero_pc", str)
            pc.push("SL6 3YZ", "test")
            loc.push(GeoPoint(51.5, -0.1), "test")

            walk_leg = JourneyLeg(
                mode=LegMode.WALK, duration=Quantity(30, "minute"), end_station="Maidenhead Rail Station"
            )
            train_leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(30, "minute"), end_station="London Paddington")
            group = CostGroup(legs=(walk_leg, train_leg), operator="TfL", cost=None)
            c = Commute(
                person=Person(name="Simon", has_car=True),
                label="Office",
                destination=PlaceOfInterest("Office", "SW1V 2QQ"),
                duration=Quantity(60, "minute"),
                daily_cost=Money("0", "GBP"),  # TfL returns 0 for NR
                mode="transit",
                _details=(group,),
            )
            transit.push(c, "test")

            node = ParkAndRideAugmentNode(
                "t_cta_zero_pr",
                transit_node=transit,
                best_location=loc,
                postcode_node=pc,
                has_car=True,
                max_walk=20,
            )

            await node.refresh()

            a = await node.attempt()
            result = a.value_or_none()
            assert result.daily_cost is not None
            # daily_cost = 0 (existing) + parking
            assert float(result.daily_cost.amount) > 0, f"Expected >0, got {result.daily_cost}"

            train_groups = [cg for cg in result.details if any(leg.mode == LegMode.TRAIN for leg in cg.legs)]
            assert len(train_groups) > 0

            train_group = train_groups[0]
            # BUG: Cost is None because existing_cost=0 skipped attribution.
            # Fix: should be 0 (not None) so frontend shows it.
            assert train_group.cost is not None, (
                f"Train CostGroup should have cost attributed (even if 0). "
                f"total={result.daily_cost}, "
                f"groups={[(cg.cost, [str(leg.mode) for leg in cg.legs]) for cg in result.details]}"
            )
        finally:
            _sp.reset(token)
