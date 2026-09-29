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


class _FakeStationLookup:
    """StationLookupService fake: returns a fixed station for any name."""

    def __init__(self, station):
        self._station = station

    def find(self, name):
        return self._station


@pytest.mark.asyncio
async def test_drive_minutes_from_location_posts_origin_coords():
    """_get_drive_minutes_from_location estimates from known coordinates
    directly — the no-postcode fallback path."""
    from houses.transit_route import _get_drive_minutes_from_location
    from tests.helpers import make_services

    fake = _FakeDirectionsClient(duration_s=720)  # 12 min
    station = type("S", (), {"location": GeoPoint(51.4, -0.97)})()
    services = make_services(station_lookup=_FakeStationLookup(station))
    result = await _get_drive_minutes_from_location(
        GeoPoint(51.5, -0.1),
        "Maidenhead Rail Station",
        _client_factory=lambda *a, **k: fake,
        _no_cache=True,
        services=services,
    )

    assert result == 12
    assert fake.posted_bodies == [{"coordinates": [[-0.1, 51.5], [-0.97, 51.4]], "units": "km"}], (
        "origin must be the known coordinates, not geocoded"
    )


@pytest.mark.asyncio
async def test_drive_minutes_from_postcode_geocodes_then_estimates():
    """_get_drive_minutes geocodes the postcode, then delegates to the
    same coords-based estimate — the two paths share the ORS call."""
    from houses.transit_route import _get_drive_minutes
    from tests.helpers import FakeGeocoder, make_services

    fake = _FakeDirectionsClient(duration_s=900)  # 15 min
    station = type("S", (), {"location": GeoPoint(51.4, -0.97)})()
    services = make_services(
        geocoder=FakeGeocoder(result=GeoPoint(51.5, -0.1)),
        station_lookup=_FakeStationLookup(station),
    )
    result = await _get_drive_minutes(
        "SL6 3YZ",
        "Maidenhead Rail Station",
        _client_factory=lambda *a, **k: fake,
        _no_cache=True,
        services=services,
    )

    assert result == 15
    assert fake.posted_bodies == [{"coordinates": [[-0.1, 51.5], [-0.97, 51.4]], "units": "km"}]


@pytest.mark.asyncio
async def test_drive_minutes_from_postcode_returns_none_when_ungeocodable():
    """An ungeocodable postcode yields None (the walk stays) — never an
    exception that could fail the commute."""
    from houses.transit_route import _get_drive_minutes
    from tests.helpers import FakeGeocoder, make_services

    services = make_services(geocoder=FakeGeocoder(result=None))
    result = await _get_drive_minutes(
        "NOT A POSTCODE",
        "Maidenhead Rail Station",
        services=services,
    )

    assert result is None
