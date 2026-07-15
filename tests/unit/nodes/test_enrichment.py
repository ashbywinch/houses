from __future__ import annotations

import pytest

import dag.user_input_node  # noqa: F401 — register Money/Quantity schemas
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
        from houses.council_tax_info import CouncilTaxInfo
        from houses.nodes.epc_node import CouncilTaxNode
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        class _FakeCT:
            async def lookup(self, postcode, address=""):
                from dag.attempt import Attempt

                return Attempt.succeeded(CouncilTaxInfo(band="D", yearly_cost=1800.0))

        svc = make_services(council_tax_service=_FakeCT())
        token = _sp.set(svc)
        try:
            addr = UserInputNode[str]("addr_ct3", str)
            pc = UserInputNode[str]("pc_ct3", str)
            node = CouncilTaxNode("ct3", best_address=addr, postcode_node=pc)
            addr.push("1 High Street, Egham, TW20 9JP", "test")
            pc.push("TW20 9JP", "test")

            from dag.derived_node import flush_processor

            await flush_processor()

            a = await node.attempt()
            assert a.succeeded
            val = a.value_or_none()
            assert val is not None
            # Must have yearly_cost, not cost
            assert "yearly_cost" in val, f"Expected 'yearly_cost' key, got {list(val.keys())}"
            assert val["yearly_cost"] == 1800.0
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


class TestTownDescNode:
    @pytest.mark.asyncio
    async def test_impossible_without_location(self):
        from houses.nodes.area import TownDescNode

        loc = UserInputNode[dict]("loc_td", dict)
        node = TownDescNode("td", best_location=loc)
        a = await node.attempt()
        assert not a.succeeded


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

        walk_leg = JourneyLeg(mode=LegMode.WALK, duration_minutes=walk_min, end_station=end_station)
        train_leg = JourneyLeg(mode=LegMode.TRAIN, duration_minutes=20, end_station="London Paddington")
        group = CostGroup(legs=(walk_leg, train_leg), operator="", cost=None)
        return Commute(
            person=Person(name="Simon", has_car=True),
            label="Office",
            destination=PlaceOfInterest("Office", "SW1V 2QQ"),
            duration=Quantity(55 if walk_min <= 55 else 55 + walk_min - 35, "minute"),
            daily_cost=Money(cost, "GBP"),
            mode="transit",
            details=(group,),
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

        from dag.derived_node import flush_processor
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
            assert val.details[0].legs[0].mode.name == "DRIVE"
            assert val.details[0].legs[0].duration_minutes == 10
            assert len(val.details) == 2
            assert val.details[1].legs[0].mode.name == "PARK"
            expected_cost = Money("21.50", "GBP")
            assert float(val.daily_cost.amount) == float(expected_cost.amount)
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_skips_short_walk(self):
        """10min walk, max_walk=20 — walk stays as walk, no parking."""
        from dag.derived_node import flush_processor
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
        assert val.details[0].legs[0].duration_minutes == 10
        # No parking group
        assert len(val.details) == 1

    @pytest.mark.asyncio
    async def test_skips_non_walking_first_leg(self):
        """First leg is train — no change."""
        from money import Money
        from pint import Quantity

        from dag.derived_node import flush_processor
        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.geo import GeoPoint

        train_leg = JourneyLeg(mode=LegMode.TRAIN, duration_minutes=20, end_station="London Paddington")
        walk_leg = JourneyLeg(mode=LegMode.WALK, duration_minutes=5, end_station="Platform 1")
        group = CostGroup(legs=(train_leg, walk_leg), operator="", cost=None)
        commute = Commute(
            person=Person(name="Simon", has_car=True),
            label="Office",
            destination=PlaceOfInterest("Office", "SW1V 2QQ"),
            duration=Quantity(25, "minute"),
            daily_cost=Money("12.50", "GBP"),
            mode="transit",
            details=(group,),
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
        from dag.derived_node import flush_processor
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
            assert val.details[0].legs[0].duration_minutes == 35
            assert len(val.details) == 1
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_format_includes_drive_in_route_after_park_and_ride(self):
        """Provenance description mentions parking after park-and-ride."""
        from dag.derived_node import flush_processor
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
