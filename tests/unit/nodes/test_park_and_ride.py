"""Tests for ParkAndRideAugmentNode — postcode vs best-location fallback.

Regression: the node hard-depended on the postcode node, so a property
with NO postcode (never scraped/entered) left the postcode UserInputNode
pending forever and every has-car commute chain (park_and_ride →
bus_augment → selector → …) was permanently stuck 'pending' instead of
computing.  The postcode must be a conditional dep, and drive time must
fall back to the best location.
"""

from __future__ import annotations

from typing import override

import pytest
from money import Money
from pint import Quantity

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.car_park import CarParkRegistry
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.geopoint import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.nodes.park_and_ride_augment_node import ParkAndRideAugmentNode, ParkAndRideOptions
from houses.stations import StationRegistry


class _FakeStationRegistry(StationRegistry):
    @override
    def find(self, name: str):
        from houses.stations import Station

        return Station(name=name, location=GeoPoint(51.4, -0.97), crs="TEST")


class _FakeCarParkRegistry(CarParkRegistry):
    @override
    def find_car_park(self, station):
        from houses.car_park import CarPark

        return CarPark(name="Test Car Park", daily_cost=Money("8.00", "GBP"))


def _walk_commute(walk_min: int = 46) -> Commute:


    return Commute(
        person=Person(name="", has_car=True),
        label="Test",
        destination=PlaceOfInterest(label="", address=""),
        duration=Quantity(walk_min + 42, "minute"),
        daily_cost=Money("0", "GBP"),
        mode="transit",
        _details=(
            CostGroup(
                legs=(
                    JourneyLeg(
                        mode=LegMode.WALK,
                        duration=Quantity(walk_min, "minute"),
                        end_station="Maidenhead Rail Station",
                    ),
                ),
            ),
            CostGroup(legs=(JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(42, "minute")),)),
        ),
    )


def _node(node_id: str, *, postcode_node, location_node):
    transit = UserInputNode[Commute](f"{node_id}_transit", Commute)
    mw = UserInputNode[int](f"{node_id}_mw", int)
    node = ParkAndRideAugmentNode(
        f"{node_id}",
        options=ParkAndRideOptions(
            transit_node=transit,
            best_location=location_node,
            postcode_node=postcode_node,
            has_car=True,
            max_walk_node=mw,
            station_registry=_FakeStationRegistry(),
            car_park_registry=_FakeCarParkRegistry(),
        ),
    )
    return node, transit, mw


class TestParkAndRideAugmentNode:
    @pytest.mark.asyncio
    async def test_pending_postcode_does_not_stall(self):
        """Regression: a pending postcode (no producer) must NOT leave
        the node stuck pending — the commute must still compute, using
        the best location for the drive-time estimate."""
        from houses.services_provider import _request_services
        from tests.helpers import FakeDriveTime, make_services

        fake_drive = FakeDriveTime(minutes=12)
        svc = make_services(drive_time_service=fake_drive)
        token = _request_services.set(svc)
        try:
            postcode = UserInputNode[str]("pp_pc", str)  # never pushed → pending forever
            location = UserInputNode[GeoPoint]("pp_loc", GeoPoint)
            location.push(GeoPoint(51.5, -0.1), "test")
            node, transit, mw = _node("pp", postcode_node=postcode, location_node=location)
            transit.push(_walk_commute(), "test")
            mw.push(30, "test")
            await flush_processor()
            a = await node.attempt()
            assert a.succeeded, f"node must compute with a pending postcode, got {a.status}: {a.error}"
            val = a.value_or_none()
            assert val is not None
            # The long walk was replaced by drive + park
            assert val.details[0].legs[0].mode == LegMode.DRIVE
            assert fake_drive.estimate_calls == [], "postcode path must NOT be used"
            assert len(fake_drive.location_calls) == 1, "best-location path must be used"
        finally:
            _request_services.reset(token)

    @pytest.mark.asyncio
    async def test_postcode_preferred_when_available(self):
        """When the postcode IS known, drive time uses it (not the
        location) — the original behaviour is preserved."""
        from houses.services_provider import _request_services
        from tests.helpers import FakeDriveTime, make_services

        fake_drive = FakeDriveTime(minutes=12)
        svc = make_services(drive_time_service=fake_drive)
        token = _request_services.set(svc)
        try:
            postcode = UserInputNode[str]("pp2_pc", str)
            postcode.push("SL6 3YZ", "test")
            location = UserInputNode[GeoPoint]("pp2_loc", GeoPoint)
            location.push(GeoPoint(51.5, -0.1), "test")
            node, transit, mw = _node("pp2", postcode_node=postcode, location_node=location)
            transit.push(_walk_commute(), "test")
            mw.push(30, "test")
            await flush_processor()
            a = await node.attempt()
            assert a.succeeded
            val = a.value_or_none()
            assert val is not None
            assert val.details[0].legs[0].mode == LegMode.DRIVE
            assert fake_drive.estimate_calls == [("SL6 3YZ", "Maidenhead Rail Station")]
            assert fake_drive.location_calls == []
        finally:
            _request_services.reset(token)

    @pytest.mark.asyncio
    async def test_short_walk_passes_commute_through_unchanged(self):
        """A walk within the tolerance never queries drive time — the
        commute passes through unchanged and stays available.  This is
        the 'no park-and-ride needed' guarantee, and it must not depend
        on the postcode having a value."""
        from houses.services_provider import _request_services
        from tests.helpers import FakeDriveTime, make_services

        fake_drive = FakeDriveTime(minutes=12)
        svc = make_services(drive_time_service=fake_drive)
        token = _request_services.set(svc)
        try:
            postcode = UserInputNode[str]("pp3_pc", str)  # pending
            location = UserInputNode[GeoPoint]("pp3_loc", GeoPoint)
            location.push(GeoPoint(51.5, -0.1), "test")
            node, transit, mw = _node("pp3", postcode_node=postcode, location_node=location)
            commute = _walk_commute(walk_min=10)  # within the 30m tolerance
            transit.push(commute, "test")
            mw.push(30, "test")
            await flush_processor()
            a = await node.attempt()
            assert a.succeeded, f"commute must stay available, got {a.status}: {a.error}"
            assert a.value_or_none() == commute
            assert fake_drive.estimate_calls == []
            assert fake_drive.location_calls == []
        finally:
            _request_services.reset(token)

    @pytest.mark.asyncio
    async def test_compute_with_neither_origin_passes_through(self):
        """Defensive guard: when compute() runs with neither a postcode
        nor a location attempt, the commute passes through unchanged —
        park-and-ride is skipped, the commute is not failed."""
        from dag.attempt import Attempt
        from houses.services_provider import _request_services
        from tests.helpers import FakeDriveTime, make_services

        fake_drive = FakeDriveTime(minutes=12)
        svc = make_services(drive_time_service=fake_drive)
        token = _request_services.set(svc)
        try:
            postcode = UserInputNode[str]("pp3b_pc", str)
            location = UserInputNode[GeoPoint]("pp3b_loc", GeoPoint)
            node, transit, mw = _node("pp3b", postcode_node=postcode, location_node=location)
            commute = _walk_commute()
            transit.push(commute, "test")
            mw.push(30, "test")
            # Both origins pending → compute must still be reachable and
            # must pass the commute through, not fail it.
            await flush_processor()
            a = await node.attempt()
            assert a.pending, "pending origins keep the node pending (transit chain)"
            direct = await node.compute(
                Attempt.succeeded(commute),
                Attempt.succeeded(30),
                Attempt.pending(),
                Attempt.pending(),
            )
            assert direct.succeeded
            assert direct.value_or_none() == commute
            assert fake_drive.estimate_calls == []
            assert fake_drive.location_calls == []
        finally:
            _request_services.reset(token)

    @pytest.mark.asyncio
    async def test_drive_replaces_walk_even_without_parking_cost(self):
        """Regression: a station with NO known parking cost made the node
        bail entirely, leaving a 76-minute walk to the station.  Driving
        is still better than walking — the walk must become a drive leg
        even when the parking cost is unknown, and the PARK leg must
        still be shown (with the car-park name; cost omitted when
        unknown) so the user can see the park-and-ride."""
        from houses.car_park import CarPark
        from houses.services_provider import _request_services
        from tests.helpers import FakeDriveTime, make_services

        class _NoCostCarParkRegistry(CarParkRegistry):
            @override
            def find_car_park(self, station):
                return CarPark(name="Reading Station Car Park", daily_cost=None)  # unknown cost

        fake_drive = FakeDriveTime(minutes=11)
        svc = make_services(drive_time_service=fake_drive)
        token = _request_services.set(svc)
        try:
            postcode = UserInputNode[str]("pp5_pc", str)
            postcode.push("RG4 9EJ", "test")
            location = UserInputNode[GeoPoint]("pp5_loc", GeoPoint)
            location.push(GeoPoint(51.5, -0.1), "test")
            transit = UserInputNode[Commute]("pp5_transit", Commute)
            mw = UserInputNode[int]("pp5_mw", int)
            node = ParkAndRideAugmentNode(
                "pp5",
                options=ParkAndRideOptions(
                    transit_node=transit,
                    best_location=location,
                    postcode_node=postcode,
                    has_car=True,
                    max_walk_node=mw,
                    station_registry=_FakeStationRegistry(),
                    car_park_registry=_NoCostCarParkRegistry(),
                ),
            )
            transit.push(_walk_commute(), "test")
            mw.push(30, "test")
            await flush_processor()
            a = await node.attempt()
            assert a.succeeded, f"got {a.status}: {a.error}"
            val = a.value_or_none()
            assert val is not None
            # The long walk became a drive leg, even though parking cost is unknown
            assert val.details[0].legs[0].mode == LegMode.DRIVE
            assert val.details[0].legs[0].duration.magnitude == 11
            # The PARK leg is STILL present — the car-park name shows the
            # park-and-ride; only the cost is omitted when unknown.
            park_groups = [g for g in val.details if any(leg.mode == LegMode.PARK for leg in g.legs)]
            assert len(park_groups) == 1, "park-and-ride leg must survive an unknown parking cost"
            assert park_groups[0].operator == "Reading Station Car Park"
            assert park_groups[0].cost is None
        finally:
            _request_services.reset(token)

    @pytest.mark.asyncio
    async def test_location_arrives_after_pending_still_recomputes(self):
        """If the node computed with a pending postcode, a postcode that
        arrives LATER must re-schedule it (the static dep signal) so the
        estimate upgrades from location-based to postcode-based."""
        from houses.services_provider import _request_services
        from tests.helpers import FakeDriveTime, make_services

        fake_drive = FakeDriveTime(minutes=12)
        svc = make_services(drive_time_service=fake_drive)
        token = _request_services.set(svc)
        try:
            postcode = UserInputNode[str]("pp4_pc", str)
            location = UserInputNode[GeoPoint]("pp4_loc", GeoPoint)
            location.push(GeoPoint(51.5, -0.1), "test")
            node, transit, mw = _node("pp4", postcode_node=postcode, location_node=location)
            transit.push(_walk_commute(), "test")
            mw.push(30, "test")
            await flush_processor()
            a = await node.attempt()
            assert a.succeeded
            assert len(fake_drive.location_calls) == 1

            # Postcode arrives later → node must recompute with it
            postcode.push("SL6 3YZ", "test")
            await flush_processor()
            a = await node.attempt()
            assert a.succeeded
            assert fake_drive.estimate_calls == [("SL6 3YZ", "Maidenhead Rail Station")]
        finally:
            _request_services.reset(token)
