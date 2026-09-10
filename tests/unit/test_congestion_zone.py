"""Regression (live 2026-09-09): the congestion-charge gate must refuse to
price a drive into the charge zone, whatever the address looks like.

Tiers, each tested: a parseable outcode in the address (SW1V → zone); an
address with no postcode at all that geocodes to an out-of-zone point
(drive fine); an address that cannot geocode at all (ERROR result —
never silently out-of-zone)."""

from __future__ import annotations

import dag.user_input_node  # noqa: F401 — register pydantic schemas
from dag.attempt import Attempt
from dag.user_input_node import UserInputNode
from houses.commute_router import CommuteRouter
from houses.geopoint import GeoPoint
from houses.model.domain import PlaceOfInterest
from houses.nodes.transit import DriveNode, RouteOptions
from houses.services_provider import _request_services as _sp
from tests.helpers import make_services
from tests.unit.conftest import flush_all


def test_outcode_check():
    assert CommuteRouter.in_congestion_zone("SW1V 2QQ") is True
    assert CommuteRouter.in_congestion_zone("RG12 8YA") is False


def test_geopoint_check():
    assert CommuteRouter.in_congestion_zone(GeoPoint(51.51, -0.1)) is True
    assert CommuteRouter.in_congestion_zone(GeoPoint(51.45, -0.99)) is False


class _FailingGeocoder:
    @staticmethod
    @staticmethod
    async def geocode_postcode(postcode: str) -> Attempt:
        return Attempt.impossible("geocode disabled")

    @staticmethod
    @staticmethod
    async def geocode_address(address: str) -> Attempt:
        return Attempt.impossible("geocode disabled")

    @staticmethod
    @staticmethod
    async def reverse_geocode_town(lat: float, lon: float) -> Attempt:
        return Attempt.impossible("geocode disabled")

    @staticmethod
    @staticmethod
    async def reverse_geocode_postcode(lat: float, lon: float) -> Attempt:
        return Attempt.impossible("geocode disabled")


def _poi_node(address: str, node_id: str) -> UserInputNode:
    poi = UserInputNode(node_id, PlaceOfInterest)
    poi.push(
        PlaceOfInterest(label="Pimlico", address=address, trips_per_week=1, weeks_per_year=46),
        "test",
    )
    return poi


def _drive_node(location: UserInputNode, poi: UserInputNode, node_id: str) -> DriveNode:
    location_node = UserInputNode(f"{node_id}_loc", GeoPoint)
    location_node.push(GeoPoint(51.45, -0.99), "test")
    return DriveNode(
        node_id,
        options=RouteOptions(
            best_location=location_node,
            poi=poi,
            has_car=True,
            max_walk=30,
        ),
    )


def test_zone_destination_drive_is_infeasible():
    poi = _poi_node("1 Drummond Gate, Pimlico, London SW1V 2QQ", "zone_poi")
    location = UserInputNode("zone_loc", GeoPoint)
    location.push(GeoPoint(51.45, -0.99), "test")
    drive = _drive_node(location, poi, "zone_gate_drive")
    flush_all()
    a = drive.latest_attempt()
    v = a.value_or_none()
    assert v is not None and v.infeasible, f"driving into the charge zone must be infeasible: {v!r}"
    assert "congestion" in v.no_route_reason


def test_no_postcode_address_is_an_error_not_a_drive():
    """An address that cannot be geocoded is an ERROR result — never a
    priced drive (the congestion rule cannot be verified)."""
    services_token = _sp.set(make_services(geocoder=_FailingGeocoder()))
    try:
        poi = _poi_node("Pimlico Rd, London", "nopc_poi")
        location = UserInputNode("nopc_loc", GeoPoint)
        location.push(GeoPoint(51.45, -0.99), "test")
        drive = _drive_node(location, poi, "nopc_gate_drive")
        flush_all()
        a = drive.latest_attempt()
        v = a.value_or_none()
        assert a.impossible, (
            f"an ungeocodable destination must be an error: {a!r}"
        )
        assert v is None
    finally:
        _sp.reset(services_token)


def test_resolvable_out_of_zone_address_keeps_its_drive():
    services_token = _sp.set(make_services(geocoder=_FailingGeocoder()))
    try:
        poi = _poi_node("Vastern Road, Reading RG1 8BT", "read_poi")
        location = UserInputNode("read_loc", GeoPoint)
        location.push(GeoPoint(51.45, -0.99), "test")
        drive = _drive_node(location, poi, "read_gate_drive")
        flush_all()
        a = drive.latest_attempt()
        v = a.value_or_none()
        assert v is not None and not v.infeasible, (
            f"an out-of-zone destination keeps its drive: {a!r}"
        )
    finally:
        _sp.reset(services_token)
