"""Tests for transit_route drive-time helpers — postcode and
location-based paths."""

from __future__ import annotations

from typing import Any

import pytest

from houses.geopoint import GeoPoint


class _FakeDirectionsClient:
    """Context manager returning a canned ORS directions response."""

    def __init__(self, duration_s: int = 720):
        self._duration_s = duration_s
        self.posted_bodies: list[Any] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, *, headers, params=None, json=None):
        self.posted_bodies.append(json)
        return _FakeResponse(self._duration_s)


class _FakeResponse:
    def __init__(self, duration_s: int, *, status_code: int = 200):
        self._duration_s = duration_s
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return {"routes": [{"summary": {"duration": self._duration_s}}]}


@pytest.mark.asyncio
async def test_drive_minutes_from_location_posts_origin_coords():
    """_get_drive_minutes_from_location estimates from known coordinates
    directly — the no-postcode fallback path."""
    from houses.transit_route import _get_drive_minutes_from_location, _LookupSeam

    fake = _FakeDirectionsClient(duration_s=720)  # 12 min
    station = type("S", (), {"location": GeoPoint(51.4, -0.97)})()
    result = await _get_drive_minutes_from_location(
        GeoPoint(51.5, -0.1),
        "Maidenhead Rail Station",
        _client_factory=lambda *a, **k: fake,
        _no_cache=True,
        _lookups=_LookupSeam(find_station=lambda name: station, geocode_address=lambda addr: None),
    )

    assert result == 12
    assert fake.posted_bodies == [{"coordinates": [[-0.1, 51.5], [-0.97, 51.4]], "units": "km"}], (
        "origin must be the known coordinates, not geocoded"
    )


@pytest.mark.asyncio
async def test_drive_minutes_from_postcode_geocodes_then_estimates():
    """_get_drive_minutes geocodes the postcode, then delegates to the
    same coords-based estimate — the two paths share the ORS call."""
    from dag.attempt import Attempt
    from houses.transit_route import _get_drive_minutes

    async def geocode_ok(addr):
        return Attempt.succeeded(GeoPoint(51.5, -0.1))

    from houses.transit_route import _LookupSeam

    fake = _FakeDirectionsClient(duration_s=900)  # 15 min
    station = type("S", (), {"location": GeoPoint(51.4, -0.97)})()
    result = await _get_drive_minutes(
        "SL6 3YZ",
        "Maidenhead Rail Station",
        _client_factory=lambda *a, **k: fake,
        _no_cache=True,
        _lookups=_LookupSeam(geocode=geocode_ok, find_station=lambda name: station, geocode_address=lambda addr: None),
    )

    assert result == 15
    assert fake.posted_bodies == [{"coordinates": [[-0.1, 51.5], [-0.97, 51.4]], "units": "km"}]


@pytest.mark.asyncio
async def test_drive_minutes_from_postcode_returns_none_when_ungeocodable():
    """An ungeocodable postcode yields None (the walk stays) — never an
    exception that could fail the commute."""
    from dag.attempt import Attempt
    from houses.transit_route import _get_drive_minutes

    async def ungeocodable(addr):
        return Attempt.impossible("no geo")

    from houses.transit_route import _LookupSeam

    result = await _get_drive_minutes(
        "NOT A POSTCODE",
        "Maidenhead Rail Station",
        _lookups=_LookupSeam(geocode=ungeocodable, geocode_address=ungeocodable),
    )

    assert result is None
