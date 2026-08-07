"""Tests for transit_route drive-time helpers — postcode and
location-based paths."""

from __future__ import annotations

import pytest

from houses.geo import GeoPoint


class _FakeDirectionsClient:
    """Context manager returning a canned ORS directions response."""

    def __init__(self, duration_s: int = 720):
        self._duration_s = duration_s
        self.posted_bodies: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, *, headers, json):
        self.posted_bodies.append(json)
        return _FakeResponse(self._duration_s)


class _FakeResponse:
    def __init__(self, duration_s: int):
        self._duration_s = duration_s

    def raise_for_status(self):
        return None

    def json(self):
        return {"routes": [{"summary": {"duration": self._duration_s}}]}


@pytest.mark.asyncio
async def test_drive_minutes_from_location_posts_origin_coords():
    """_get_drive_minutes_from_location estimates from known coordinates
    directly — the no-postcode fallback path."""
    from unittest.mock import patch

    from houses.transit_route import _get_drive_minutes_from_location

    fake = _FakeDirectionsClient(duration_s=720)  # 12 min
    with (
        patch("houses.transit_route.cached_async_client", return_value=fake),
        patch("houses.transit_route.get_cached", return_value=None),
        patch("houses.transit_route.set_cached"),
        patch("houses.transit_route.settings.ors_api_key", "fake-key"),
        patch("houses.transit_route.find_station") as find_station,
        patch("houses.transit_route._geocode_address") as geocode_address,
    ):
        find_station.return_value = type("S", (), {"location": GeoPoint(51.4, -0.97)})()
        geocode_address.return_value = None  # station found in registry, no geocode needed
        result = await _get_drive_minutes_from_location(GeoPoint(51.5, -0.1), "Maidenhead Rail Station")

    assert result == 12
    assert fake.posted_bodies == [
        {"coordinates": [[-0.1, 51.5], [-0.97, 51.4]], "units": "km"}
    ], "origin must be the known coordinates, not geocoded"


@pytest.mark.asyncio
async def test_drive_minutes_from_postcode_geocodes_then_estimates():
    """_get_drive_minutes geocodes the postcode, then delegates to the
    same coords-based estimate — the two paths share the ORS call."""
    from unittest.mock import patch

    from dag.attempt import Attempt
    from houses.transit_route import _get_drive_minutes

    fake = _FakeDirectionsClient(duration_s=900)  # 15 min
    with (
        patch("houses.transit_route.cached_async_client", return_value=fake),
        patch("houses.transit_route.get_cached", return_value=None),
        patch("houses.transit_route.set_cached"),
        patch("houses.transit_route.settings.ors_api_key", "fake-key"),
        patch("houses.transit_route.geocode") as geocode,
        patch("houses.transit_route.find_station") as find_station,
        patch("houses.transit_route._geocode_address") as geocode_address,
    ):
        geocode.return_value = Attempt.succeeded(GeoPoint(51.5, -0.1))
        find_station.return_value = type("S", (), {"location": GeoPoint(51.4, -0.97)})()
        geocode_address.return_value = None
        result = await _get_drive_minutes("SL6 3YZ", "Maidenhead Rail Station")

    assert result == 15
    assert fake.posted_bodies == [
        {"coordinates": [[-0.1, 51.5], [-0.97, 51.4]], "units": "km"}
    ]


@pytest.mark.asyncio
async def test_drive_minutes_from_postcode_returns_none_when_ungeocodable():
    """An ungeocodable postcode yields None (the walk stays) — never an
    exception that could fail the commute."""
    from unittest.mock import patch

    from dag.attempt import Attempt
    from houses.transit_route import _get_drive_minutes

    with (
        patch("houses.transit_route.geocode") as geocode,
        patch("houses.transit_route._geocode_address") as geocode_address,
    ):
        geocode.return_value = Attempt.impossible("no geo")
        geocode_address.return_value = Attempt.impossible("no geo")
        result = await _get_drive_minutes("NOT A POSTCODE", "Maidenhead Rail Station")

    assert result is None
