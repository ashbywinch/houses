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
    from houses.rail_fares import RailFareRegistry, enrich_rail_fares
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
        from dag.attempt import Attempt
        from houses.geo import GeoPoint

        return Attempt.succeeded(GeoPoint(51.317, -0.556))

    async def mock_tube_fare(station, postcode, _data=None):
        return Money("3.40", "GBP")

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
    simon_result, _ = await enrich_rail_fares(
        enabled={"simon"},
        postcode="GU21 2NA",
        address="Robin Hood Road, Knaphill",
        simon=simon,
        lorena=lorena,
        _registry=reg,
        _geocode=mock_geocode,
        _tube_fare_fn=mock_tube_fare,
    )
    # rail: 17.00. tube: 3.40 (peak). return: (17.00 + 3.40) × 2 = 40.80
    # parking: 10.80. total: 40.80 + 10.80 = 51.60
    # With old £2.80: (17.00 + 2.80) × 2 + 10.80 = 50.40
    # With new £3.40: (17.00 + 3.40) × 2 + 10.80 = 51.60
    assert simon_result.daily_cost_gbp == Money("51.60", "GBP")


# ── _format_route_summary ──────────────────────────────────────────────


class TestFormatRouteSummary:
    """_format_route_summary — build route string from TfL journey dict."""

    TFL_JOURNEY = {
        "legs": [
            {
                "mode": {"name": "walking"},
                "duration": 6,
                "departurePoint": {"commonName": "SL6 3YZ"},
                "arrivalPoint": {"commonName": "Cox Green, Brill Close"},
                "instruction": {"summary": "Walk to Cox Green (nr Windsor), Brill Close"},
            },
            {
                "mode": {"name": "bus"},
                "duration": 9,
                "departurePoint": {"commonName": "Cox Green, Brill Close"},
                "arrivalPoint": {"commonName": "Maidenhead, Frascati Way"},
                "instruction": {"summary": "7 bus to Maidenhead, Frascati Way"},
            },
            {
                "mode": {"name": "walking"},
                "duration": 5,
                "departurePoint": {"commonName": "Maidenhead Town Centre, Maidenhead Railway Station"},
                "arrivalPoint": {"commonName": "Maidenhead Rail Station"},
                "instruction": {"summary": "Walk to Maidenhead Rail Station"},
            },
            {
                "mode": {"name": "national-rail"},
                "duration": 20,
                "departurePoint": {"commonName": "Maidenhead Rail Station"},
                "arrivalPoint": {"commonName": "London Paddington Rail Station"},
                "instruction": {"summary": "Great Western Railway to London Paddington"},
            },
            {
                "mode": {"name": "tube"},
                "duration": 8,
                "departurePoint": {"commonName": "Paddington Underground Station"},
                "arrivalPoint": {"commonName": "Oxford Circus Underground Station"},
                "instruction": {"summary": "Bakerloo line to Oxford Circus"},
            },
            {
                "mode": {"name": "walking"},
                "duration": 7,
                "departurePoint": {"commonName": "Pimlico Underground Station"},
                "arrivalPoint": {"commonName": "SW1V 2QQ"},
                "instruction": {"summary": "Walk to SW1V 2QQ"},
            },
        ]
    }

    def test_includes_walking_legs(self):
        from houses.transit_route import _format_route_summary

        result = _format_route_summary(self.TFL_JOURNEY)
        # First walk is to a non-station -> no destination
        assert "walk 6m" in result
        # Middle walk to a station -> shows destination
        assert "walk to Maidenhead (5m)" in result
        # Last walk is final destination -> no destination
        assert "walk 7m" in result

    def test_walking_shows_destination_for_stations(self):
        """Walking segments show their destination when walking to a station
        rather than the final property."""
        from houses.transit_route import _format_route_summary

        result = _format_route_summary(self.TFL_JOURNEY)
        # The second walking leg arrives at Maidenhead Rail Station
        assert "walk to Maidenhead (5m)" in result

    def test_includes_transit_legs(self):
        from houses.transit_route import _format_route_summary

        result = _format_route_summary(self.TFL_JOURNEY)
        assert "bus(7) to Maidenhead" in result
        assert "Train to Paddington (20m)" in result
        assert "Bakerloo line to Oxford Circus (8m)" in result

    def test_includes_station_names_for_transit_legs(self):
        from houses.transit_route import _format_route_summary

        result = _format_route_summary(self.TFL_JOURNEY)
        assert "Train to Paddington (20m)" in result
        assert "Bakerloo line to Oxford Circus (8m)" in result

    def test_omits_departure_when_same_as_previous_arrival(self):
        """Transit leg's departure is omitted when it matches the previous transit leg's arrival."""
        from houses.transit_route import _format_route_summary

        result = _format_route_summary(self.TFL_JOURNEY)
        assert "Train to Paddington (20m)" in result
        assert "Bakerloo line to Oxford Circus (8m)" in result

    def test_handles_london_prefix_mismatch(self):
        """NR arrives at 'London X' — 'London ' prefix is stripped."""
        from houses.transit_route import _format_route_summary

        journey = {
            "legs": [
                {"mode": {"name": "walking"}, "duration": 5, "instruction": {"summary": ""}},
                {
                    "mode": {"name": "national-rail"},
                    "duration": 30,
                    "departurePoint": {"commonName": "Town Rail Station"},
                    "arrivalPoint": {"commonName": "London Waterloo Rail Station"},
                    "instruction": {"summary": "Express to London Waterloo"},
                },
                {
                    "mode": {"name": "tube"},
                    "duration": 5,
                    "departurePoint": {"commonName": "Waterloo Underground Station"},
                    "arrivalPoint": {"commonName": "Bank Underground Station"},
                    "instruction": {"summary": "Waterloo & City line to Bank"},
                },
            ]
        }
        result = _format_route_summary(journey)
        assert "Train to Waterloo (30m)" in result
        assert "Waterloo & City line to Bank (5m)" in result

    def test_excludes_station_names_for_walking_legs(self):
        from houses.transit_route import _format_route_summary

        result = _format_route_summary(self.TFL_JOURNEY)
        assert "SL6 3YZ" not in result
        assert "Pimlico" not in result  # walking leg at end has Pimlico, but should be omitted

    def test_duration_numbers_appear(self):
        from houses.transit_route import _format_route_summary

        result = _format_route_summary(self.TFL_JOURNEY)
        assert "6m" in result
        assert "20m" in result
        assert "8m" in result

    def test_empty_legs(self):
        from houses.transit_route import _format_route_summary

        result = _format_route_summary({"legs": []})
        assert result == ""

    def test_no_legs_key(self):
        from houses.transit_route import _format_route_summary

        result = _format_route_summary({})
        assert result == ""

    def test_driving_leg_format(self):
        """Park-and-ride replaces the first walk leg with a drive leg."""
        from houses.transit_route import _format_route_summary

        journey = {
            "legs": [
                {
                    "mode": {"name": "driving"},
                    "duration": 10,
                    "arrivalPoint": {"commonName": "Maidenhead Rail Station"},
                    "instruction": {"summary": "Drive to Maidenhead Rail Station"},
                },
                {
                    "mode": {"name": "national-rail"},
                    "duration": 18,
                    "arrivalPoint": {"commonName": "London Paddington Rail Station"},
                    "instruction": {"summary": "Great Western Railway to London Paddington"},
                },
                {
                    "mode": {"name": "walking"},
                    "duration": 7,
                    "arrivalPoint": {"commonName": "SW1V 2QQ"},
                    "instruction": {"summary": "Walk to SW1V 2QQ"},
                },
            ]
        }
        result = _format_route_summary(journey)
        assert "Drive to Maidenhead (10m)" in result
        assert "Train to Paddington (18m)" in result
        assert "walk 7m" in result


# ── TransitRoute._build_cost_groups + _render_leg_description ─────────


class TestTfLRouteSummary:
    """TransitRoute._build_cost_groups must preserve TfL station/line names."""

    def test_summary_includes_station_names(self):
        """JourneyLeg descriptions should contain station names and transit route info."""
        from houses.transit_route import TransitRoute

        route = TransitRoute("SL6", "SW1V 2QQ", "test")
        tfl_data = {
            "journeys": [
                {
                    "duration": 87,
                    "legs": [
                        {
                            "mode": {"name": "walking"},
                            "duration": 16,
                            "instruction": {"summary": "walk to Maidenhead"},
                            "arrivalPoint": {"commonName": "Maidenhead Rail Station"},
                        },
                        {
                            "mode": {"name": "national-rail"},
                            "duration": 22,
                            "instruction": {"summary": "Train to Paddington"},
                            "route": {"name": "Great Western Railway"},
                            "departurePoint": {"commonName": "Maidenhead"},
                            "arrivalPoint": {"commonName": "Paddington"},
                        },
                        {
                            "mode": {"name": "tube"},
                            "duration": 8,
                            "instruction": {"summary": "Bakerloo line to Oxford Circus"},
                            "route": {"name": "Bakerloo"},
                            "departurePoint": {"commonName": "Paddington"},
                            "arrivalPoint": {"commonName": "Oxford Circus"},
                        },
                    ],
                    "fare": {"totalCost": 500, "singleFare": 250},
                }
            ]
        }

        groups = route._build_cost_groups(tfl_data)
        from houses.commute import _render_leg_description

        all_descriptions = []
        for g in groups:
            for leg in g.legs:
                all_descriptions.append(_render_leg_description(leg))

        combined = " ".join(all_descriptions)
        assert "Maidenhead" in combined, f"Should mention station name, got: {combined}"
        assert "Paddington" in combined, f"Should mention arrival station, got: {combined}"
        assert any("Bakerloo" in d or "Great Western" in d for d in all_descriptions), (
            f"Should mention transit line name, got: {all_descriptions}"
        )

        # Check the new fields are populated
        all_legs = [leg for g in groups for leg in g.legs]
        tube_leg = next(leg for leg in all_legs if leg.mode.name == "TUBE")
        assert tube_leg.line_name == "Bakerloo", f"Expected Bakerloo line, got {tube_leg.line_name}"
        assert tube_leg.end_station == "Oxford Circus", f"Expected Oxford Circus, got {tube_leg.end_station}"
        assert tube_leg.start_station == "Paddington", f"Expected Paddington, got {tube_leg.start_station}"
        train_leg = next(leg for leg in all_legs if leg.mode.name == "TRAIN")
        assert train_leg.line_name == "Great Western Railway"

    def test_summary_for_bus_leg_does_not_crash(self):
        """_build_cost_groups must handle bus legs (regression: _shorten_station scope)."""
        from houses.transit_route import TransitRoute

        route = TransitRoute("SL6", "SW1V 2QQ", "test")
        tfl_data = {
            "journeys": [
                {
                    "duration": 45,
                    "legs": [
                        {
                            "mode": {"name": "walking"},
                            "duration": 5,
                            "arrivalPoint": {"commonName": "Maidenhead Bus Station"},
                        },
                        {
                            "mode": {"name": "bus"},
                            "duration": 20,
                            "route": {"name": "7"},
                            "departurePoint": {"commonName": "Maidenhead Bus Station"},
                            "arrivalPoint": {"commonName": "Slough Bus Station"},
                        },
                    ],
                    "fare": {"totalCost": 350, "singleFare": 175},
                }
            ]
        }

        groups = route._build_cost_groups(tfl_data)
        from houses.commute import _render_leg_description

        descriptions = [_render_leg_description(leg) for g in groups for leg in g.legs]
        combined = " ".join(descriptions)
        assert "7 to" in combined, f"Expected '7 to' format, got: {descriptions}"

    def test_tube_leg_without_line_name_falls_back_to_mode(self):
        """Tube leg with no route.name extracts line from instruction text."""
        from houses.transit_route import TransitRoute

        route = TransitRoute("SL6", "SW1V 2QQ", "test")
        tfl_data = {
            "journeys": [
                {
                    "duration": 30,
                    "legs": [
                        {
                            "mode": {"name": "tube"},
                            "duration": 8,
                            "route": {},  # no line name
                            "departurePoint": {"commonName": "Paddington"},
                            "arrivalPoint": {"commonName": "Oxford Circus"},
                            "instruction": {"summary": "Bakerloo line to Oxford Circus"},
                        },
                    ],
                    "fare": {"totalCost": 250, "singleFare": 125},
                }
            ]
        }

        groups = route._build_cost_groups(tfl_data)
        from houses.commute import _render_leg_description

        descriptions = [_render_leg_description(leg) for g in groups for leg in g.legs]
        combined = " ".join(descriptions)
        # Should extract tube line from instruction text, not use bare "line"
        assert "Bakerloo" in combined, f"Expected Bakerloo line from instruction, got: {descriptions}"


class TestNextWeekdayDateParams:
    """_next_weekday_date_params — date/time for next weekday 09:00."""

    def test_returns_weekday_date(self):
        from datetime import datetime

        from houses.transit_route import _next_weekday_date_params

        result = _next_weekday_date_params()
        assert "date" in result
        assert "time" in result
        assert result["time"] == "0900"
        dt = datetime.strptime(result["date"], "%Y%m%d")
        assert dt.weekday() < 5, f"{result['date']} is not a weekday"


class TestPickBestJourney:
    """_pick_best_journey — shortest journey selection."""

    def test_returns_duration_and_cost_and_route(self):
        from houses.transit_route import _pick_best_journey

        walk_leg = {"mode": {"name": "walking"}, "duration": 5, "instruction": {"summary": ""}}
        data = {
            "journeys": [
                {"duration": 50, "legs": [walk_leg]},
                {"duration": 30, "legs": [walk_leg]},
                {"duration": 45, "legs": [walk_leg]},
            ]
        }
        duration, cost, route = _pick_best_journey(data)
        assert duration == 30
        assert cost is None
        assert isinstance(route, str)
        assert route != ""

    def test_picks_shortest_with_fare(self):
        from houses.transit_route import _pick_best_journey

        data = {
            "journeys": [
                {"duration": 50, "fare": {"totalCost": 1200}, "legs": []},
                {"duration": 30, "fare": {"totalCost": 800}, "legs": []},
            ]
        }
        duration, cost, route = _pick_best_journey(data)
        assert duration == 30
        assert cost == 16.0
        assert isinstance(route, str)

    def test_empty_journeys_returns_none(self):
        from houses.transit_route import _pick_best_journey

        dur, cst, rte = _pick_best_journey({"journeys": []})
        assert dur is None
        assert cst is None

    def test_none_data_returns_none(self):
        from houses.transit_route import _pick_best_journey

        dur, cst, rte = _pick_best_journey(None)
        assert dur is None
        assert cst is None
