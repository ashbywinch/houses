"""Park-and-ride end-to-end through the real production wiring.

``TflClient.plan`` → ``apply_park_and_ride_to_journeys`` → the Services
container's drive estimate (geocoder + station lookup + ORS directions).
Regression for 2026-09-29: the drive helpers used to carry a fallback
branch that only the tests covered while production ran the other; now
production and tests run the SAME container path, and this test walks the
whole chain to prove it.
"""

from __future__ import annotations

import pytest
from httpx import Response

from houses.commute import LegMode
from houses.geopoint import GeoPoint
from houses.tfl_client import TflClient, TflRouteOptions
from tests.helpers import FakeGeocoder, make_services

pytestmark = pytest.mark.asyncio

_MAX_WALK_MINUTES = 20  # settings default for max_walk_to_station


# lucidlint: ignore record-shape the TfL provider payload is a test fixture of
# the provider wire shape — the caller parses it at the boundary (tfl_client)
def _walk_too_long_payload() -> dict:
    """One journey: a 45-minute walk to Maidenhead station — over the
    20-minute threshold, so park-and-ride must replace it with driving."""
    return {
        "journeys": [
            {
                "duration": 87,
                "legs": [
                    {
                        "mode": {"name": "walking"},
                        "duration": 45,
                        "isTimeline": True,
                        "arrivalPoint": {"commonName": "Maidenhead Rail Station"},
                    }
                ],
                "fare": {"totalCost": 500, "singleFare": 250},
            }
        ]
    }


async def test_park_and_ride_drive_replaces_walk(_mock_http_requests):
    """The ORS directions mock answers 1800s (= 30 min), so the replaced
    driving leg must carry 30 min — proving the container's drive estimate
    (not a stub) produced it."""
    _mock_http_requests.add_rule(
        lambda url: "tfl.gov.uk" in str(url),
        lambda request: Response(200, json=_walk_too_long_payload()),
    )
    services = make_services(geocoder=FakeGeocoder(result=GeoPoint(51.5, -0.1)))
    client = TflClient(
        "SL6 3YZ",
        "SW1P 1AA",
        "test",
        options=TflRouteOptions(park_and_ride=True, services=services),
    )
    result = await client.plan()

    assert result.succeeded
    commute = result.value_or_none()
    assert commute is not None
    groups = commute._details
    assert groups, "the commute must carry the journey legs"
    first_leg = groups[0].legs[0]
    assert first_leg.mode == LegMode.DRIVE, f"walk must be replaced by driving, got {first_leg.mode}"
    assert first_leg.duration.magnitude == 30, (
        f"drive leg must be the ORS-mocked 30 minutes, got {first_leg.duration.magnitude}"
    )
