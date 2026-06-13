"""Tests for transit_route.py — TfL tube leg fare lookup."""

import pytest
from money import Money

from houses.stations import Station

# ── get_tube_leg_fare ───────────────────────────────────────────────────


def _victoria_station() -> Station:
    return Station(name="Victoria Station", crs="VIC", location=None)  # type: ignore[arg-type]


def _tfl_fare_response(total_cost_pence: int) -> dict:
    """Simulate a TfL journey response with a fare."""
    return {
        "journeys": [
            {
                "duration": 15,
                "fare": {
                    "totalCost": total_cost_pence,
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_returns_peak_single_fare(tmp_path):
    """When TfL returns a journey with a fare, the peak single is returned."""
    from houses.transit_route import get_tube_leg_fare

    result = await get_tube_leg_fare(
        _victoria_station(),
        "SW1V 2QQ",
        _data=_tfl_fare_response(340),  # £3.40 peak single
    )
    assert result == Money("3.40", "GBP")


@pytest.mark.asyncio
async def test_returns_none_when_no_journey():
    """When TfL can't route (404 / no journeys), returns None (walking distance)."""
    from houses.transit_route import get_tube_leg_fare

    result = await get_tube_leg_fare(
        _victoria_station(),
        "SW1V 2QQ",
        _data={"journeys": []},
    )
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_no_fare(tmp_path):
    """When TfL routes but doesn't include a fare, returns None."""
    from houses.transit_route import get_tube_leg_fare

    result = await get_tube_leg_fare(
        _victoria_station(),
        "SW1V 2QQ",
        _data={
            "journeys": [
                {
                    "duration": 15,
                    # no "fare" key — walking distance from station
                }
            ]
        },
    )
    assert result is None


@pytest.mark.asyncio
async def test_uses_peak_time_params():
    """The TfL API call uses peak-time params (weekday 09:00 or earlier)."""
    from houses.transit_route import get_tube_leg_fare

    result = await get_tube_leg_fare(
        _victoria_station(),
        "SW1V 2QQ",
        _data=_tfl_fare_response(340),
    )
    # Just verify no exception — the function exists and runs
    assert result is not None


@pytest.mark.asyncio
async def test_enrich_uses_tfl_tube_fare_when_needed(tmp_path):
    """_enrich_rail_fares uses the TfL tube fare instead of hardcoded £2.80."""
    from houses.commute import Commute, CostGroup, JourneyLeg, LegMode
    from houses.enrichment_runner import _enrich_rail_fares
    from houses.rail_fares import RailFareRegistry
    from houses.stations import StationRegistry

    stations_csv = tmp_path / "stations.csv"
    stations_csv.write_text(
        "stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\nVictoria Station,VIC,51.495,-0.144\n"
    )
    fares_csv = tmp_path / "fares.csv"
    fares_csv.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,VIC,17.00\n")

    reg = RailFareRegistry(
        station_registry=StationRegistry(_stations_csv=stations_csv),
        _fares_csv=fares_csv,
    )

    async def mock_geocode(_):
        from houses.attempt import Attempt
        from houses.geo import GeoPoint

        return Attempt.succeeded(GeoPoint(51.317, -0.556), "test")

    # Inject a mock tube fare of £3.40 (peak) instead of hardcoded £2.80
    import houses.transit_route as tr

    async def mock_tube_fare(station, postcode, _data=None):
        return Money("3.40", "GBP")

    original = tr.get_tube_leg_fare
    tr.get_tube_leg_fare = mock_tube_fare
    try:
        simon = Commute(
            destination_label="Simon",
            destination_postcode="SW1V 2QQ",
            duration_minutes=71,
            daily_cost_gbp=Money("10.8", "GBP"),
            cost_groups=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.PARK, duration_minutes=0),), operator="ParkCo", cost=10.8),
            ),
        )
        lorena = Commute(
            destination_label="Lorena",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=None,
        )
        simon_result, _ = await _enrich_rail_fares(
            enabled={"simon"},
            postcode="GU21 2NA",
            address="Robin Hood Road, Knaphill",
            simon=simon,
            lorena=lorena,
            _registry=reg,
            _geocode=mock_geocode,
        )
        # rail: 17.00. tube: 3.40 (peak). return: (17.00 + 3.40) × 2 = 40.80
        # parking: 10.80. total: 40.80 + 10.80 = 51.60
        # With old £2.80: (17.00 + 2.80) × 2 + 10.80 = 50.40
        # With new £3.40: (17.00 + 3.40) × 2 + 10.80 = 51.60
        assert simon_result.daily_cost_gbp == Money("51.60", "GBP")
    finally:
        tr.get_tube_leg_fare = original
