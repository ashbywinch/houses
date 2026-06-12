"""Integration tests for enricher — park-and-ride."""

import copy
from unittest.mock import patch

import pytest

from houses.enricher import _apply_park_and_ride_to_journeys, _format_route_summary


class TestParkAndRide:
    """_apply_park_and_ride_to_journeys — replaces long walks with driving."""

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

    @pytest.mark.asyncio
    async def test_replaces_long_walk_with_drive(self):
        data = copy.deepcopy(self.LONG_WALK_DATA)
        with patch("houses.enricher._get_drive_minutes", return_value=10):
            result = await _apply_park_and_ride_to_journeys(data, "SL6 3YZ", max_walk_minutes=20)
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "driving"
        assert result["journeys"][0]["duration"] == 62

    @pytest.mark.asyncio
    async def test_skips_short_walk(self):
        data = copy.deepcopy(self.SHORT_WALK_DATA)
        with patch("houses.enricher._get_drive_minutes", return_value=3):
            result = await _apply_park_and_ride_to_journeys(data, "KT13 0TD", max_walk_minutes=20)
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "walking"
        assert legs[0]["duration"] == 10

    @pytest.mark.asyncio
    async def test_skips_non_walking_first_leg(self):
        data = {"journeys": [{"duration": 45, "legs": [{"mode": {"name": "national-rail"}, "duration": 20}]}]}
        with patch("houses.enricher._get_drive_minutes") as mock_drive:
            result = await _apply_park_and_ride_to_journeys(data, "SL6", 20)
        mock_drive.assert_not_called()
        assert result["journeys"][0]["legs"][0]["mode"]["name"] == "national-rail"

    @pytest.mark.asyncio
    async def test_skips_when_drive_lookup_fails(self):
        data = copy.deepcopy(self.LONG_WALK_DATA)
        with patch("houses.enricher._get_drive_minutes", return_value=None):
            result = await _apply_park_and_ride_to_journeys(data, "SL6 3YZ", max_walk_minutes=20)
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "walking"
        assert legs[0]["duration"] == 35

    @pytest.mark.asyncio
    async def test_format_includes_drive_in_route_after_park_and_ride(self):
        data = copy.deepcopy(self.LONG_WALK_DATA)
        with patch("houses.enricher._get_drive_minutes", return_value=10):
            result = await _apply_park_and_ride_to_journeys(data, "SL6 3YZ", max_walk_minutes=20)
        best = min(result["journeys"], key=lambda j: j.get("duration", 9999))
        summary = _format_route_summary(best)
        assert "Drive to Maidenhead (10m)" in summary
        assert "Train to Paddington (20m)" in summary
        assert "walk 7m" in summary
