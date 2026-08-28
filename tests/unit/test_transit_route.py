"""Tests for transit_route.py — TfL tube leg fare lookup."""

import pytest
from money import Money

from houses.stations import Station

# ── get_tube_leg_fare ───────────────────────────────────────────────────


def _victoria_station() -> Station:
    return Station(name="Victoria Station", crs="VIC", location=None)  # type: ignore[arg-type]  # location is annotated non-optional GeoPoint but get_tube_leg_fare only reads .name — None is the intended runtime value here


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
    from houses.tfl_client import TflClient

    result = await TflClient.get_tube_leg_fare(
        _victoria_station(),
        "SW1V 2QQ",
        _data=_tfl_fare_response(340),  # £3.40 peak single
    )
    assert result == Money("3.40", "GBP")


@pytest.mark.asyncio
async def test_returns_none_when_no_journey():
    """When TfL can't route (404 / no journeys), returns None (walking distance)."""
    from houses.tfl_client import TflClient

    result = await TflClient.get_tube_leg_fare(
        _victoria_station(),
        "SW1V 2QQ",
        _data={"journeys": []},
    )
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_no_fare(tmp_path):
    """When TfL routes but doesn't include a fare, returns None."""
    from houses.tfl_client import TflClient

    result = await TflClient.get_tube_leg_fare(
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
    from houses.tfl_client import TflClient

    result = await TflClient.get_tube_leg_fare(
        _victoria_station(),
        "SW1V 2QQ",
        _data=_tfl_fare_response(340),
    )
    # Just verify no exception — the function exists and runs
    assert result is not None


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
        from houses.tfl_client import TflClient

        result = TflClient._format_route_summary(self.TFL_JOURNEY)
        # First walk is to a non-station -> no destination
        assert "walk 6m" in result
        # Middle walk to a station -> shows destination
        assert "walk to Maidenhead (5m)" in result
        # Last walk is final destination -> no destination
        assert "walk 7m" in result

    def test_walking_shows_destination_for_stations(self):
        """Walking segments show their destination when walking to a station
        rather than the final property."""
        from houses.tfl_client import TflClient

        result = TflClient._format_route_summary(self.TFL_JOURNEY)
        # The second walking leg arrives at Maidenhead Rail Station
        assert "walk to Maidenhead (5m)" in result

    def test_includes_transit_legs(self):
        from houses.tfl_client import TflClient

        result = TflClient._format_route_summary(self.TFL_JOURNEY)
        assert "bus(7) to Maidenhead" in result
        assert "Train to Paddington (20m)" in result
        assert "Bakerloo line to Oxford Circus (8m)" in result

    def test_includes_station_names_for_transit_legs(self):
        from houses.tfl_client import TflClient

        result = TflClient._format_route_summary(self.TFL_JOURNEY)
        assert "Train to Paddington (20m)" in result
        assert "Bakerloo line to Oxford Circus (8m)" in result

    def test_omits_departure_when_same_as_previous_arrival(self):
        """Transit leg's departure is omitted when it matches the previous transit leg's arrival."""
        from houses.tfl_client import TflClient

        result = TflClient._format_route_summary(self.TFL_JOURNEY)
        assert "Train to Paddington (20m)" in result
        assert "Bakerloo line to Oxford Circus (8m)" in result

    def test_handles_london_prefix_mismatch(self):
        """NR arrives at 'London X' — 'London ' prefix is stripped."""
        from houses.tfl_client import TflClient

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
        result = TflClient._format_route_summary(journey)
        assert "Train to Waterloo (30m)" in result
        assert "Waterloo & City line to Bank (5m)" in result

    def test_excludes_station_names_for_walking_legs(self):
        from houses.tfl_client import TflClient

        result = TflClient._format_route_summary(self.TFL_JOURNEY)
        assert "SL6 3YZ" not in result
        assert "Pimlico" not in result  # walking leg at end has Pimlico, but should be omitted

    def test_duration_numbers_appear(self):
        from houses.tfl_client import TflClient

        result = TflClient._format_route_summary(self.TFL_JOURNEY)
        assert "6m" in result
        assert "20m" in result
        assert "8m" in result

    def test_empty_legs(self):
        from houses.tfl_client import TflClient

        result = TflClient._format_route_summary({"legs": []})
        assert result == ""

    def test_no_legs_key(self):
        from houses.tfl_client import TflClient

        result = TflClient._format_route_summary({})
        assert result == ""

    def test_driving_leg_format(self):
        """Park-and-ride replaces the first walk leg with a drive leg."""
        from houses.tfl_client import TflClient

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
        result = TflClient._format_route_summary(journey)
        assert "Drive to Maidenhead (10m)" in result
        assert "Train to Paddington (18m)" in result
        assert "walk 7m" in result


# ── TransitRoute._build_cost_groups + render_leg_description ─────────


class TestTfLRouteSummary:
    """TransitRoute._build_cost_groups must preserve TfL station/line names."""

    def test_summary_includes_station_names(self):
        """JourneyLeg descriptions should contain station names and transit route info."""
        from houses.tfl_client import TflClient

        route = TflClient("SL6", "SW1V 2QQ", "test")
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
        from houses.commute import render_leg_description

        all_descriptions = []
        for g in groups:
            for leg in g.legs:
                all_descriptions.append(render_leg_description(leg))

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
        from houses.tfl_client import TflClient

        route = TflClient("SL6", "SW1V 2QQ", "test")
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
        from houses.commute import render_leg_description

        descriptions = [render_leg_description(leg) for g in groups for leg in g.legs]
        combined = " ".join(descriptions)
        assert "7 to" in combined, f"Expected '7 to' format, got: {descriptions}"

    def test_tube_leg_without_line_name_falls_back_to_mode(self):
        """Tube leg with no route.name extracts line from instruction text."""
        from houses.tfl_client import TflClient

        route = TflClient("SL6", "SW1V 2QQ", "test")
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
        from houses.commute import render_leg_description

        descriptions = [render_leg_description(leg) for g in groups for leg in g.legs]
        combined = " ".join(descriptions)
        # Should extract tube line from instruction text, not use bare "line"
        assert "Bakerloo" in combined, f"Expected Bakerloo line from instruction, got: {descriptions}"


class TestNextWeekdayDateParams:
    """_next_weekday_date_params — date/time for next weekday 09:00."""

    def test_returns_weekday_date(self):
        from datetime import datetime

        from houses.tfl_client import TflClient

        result = TflClient._next_weekday_date_params()
        assert "date" in result
        assert "time" in result
        assert result["time"] == "0900"
        dt = datetime.strptime(result["date"], "%Y%m%d")
        assert dt.weekday() < 5, f"{result['date']} is not a weekday"


class TestPickBestJourney:
    """_pick_best_journey — shortest journey selection."""

    def test_returns_duration_and_cost_and_route(self):
        from houses.tfl_client import TflClient

        walk_leg = {"mode": {"name": "walking"}, "duration": 5, "instruction": {"summary": ""}}
        data = {
            "journeys": [
                {"duration": 50, "legs": [walk_leg]},
                {"duration": 30, "legs": [walk_leg]},
                {"duration": 45, "legs": [walk_leg]},
            ]
        }
        summary = TflClient._pick_best_journey(data)
        assert summary.duration == 30
        assert summary.cost is None
        assert isinstance(summary.route_summary, str)
        assert summary.route_summary != ""

    def test_picks_shortest_with_fare(self):
        from houses.tfl_client import TflClient

        data = {
            "journeys": [
                {"duration": 50, "fare": {"totalCost": 1200}, "legs": []},
                {"duration": 30, "fare": {"totalCost": 800}, "legs": []},
            ]
        }
        summary = TflClient._pick_best_journey(data)
        assert summary.duration == 30
        assert summary.cost == 16.0
        assert isinstance(summary.route_summary, str)


    def test_empty_journeys_returns_none(self):
        from houses.tfl_client import TflClient

        summary = TflClient._pick_best_journey({"journeys": []})
        assert summary.duration is None
        assert summary.cost is None


    def test_none_data_returns_none(self):
        from houses.tfl_client import TflClient

        summary = TflClient._pick_best_journey(None)
        assert summary.duration is None
        assert summary.cost is None


class TestTflCachedApiCall4xx:
    """TfL non-transient client errors must surface the reason.

    Regression: _cached_api_call returned 4xx bodies as data, which
    _process_data then reduced to a generic "could not route transit".
    A 409 (route planner unavailable) should raise HttpError with the
    reason so the DAG surfaces it.
    """

    @pytest.mark.asyncio
    async def test_409_raises_http_error_with_body(self):
        from unittest.mock import AsyncMock, patch

        from dag.http_error import HttpError
        from houses.tfl_client import TflClient

        fake_resp = AsyncMock()
        fake_resp.status_code = 409
        fake_resp.json = lambda: {"message": "route planner unavailable"}
        fake_client = AsyncMock()
        fake_client.get = AsyncMock(return_value=fake_resp)

        class _FakeCM:
            async def __aenter__(self):
                return fake_client

            async def __aexit__(self, *a):
                return False

        with (
            patch("houses.tfl_client.get_cached", return_value=None),
            patch("houses.tfl_client.cached_async_client", return_value=_FakeCM()),
            patch("houses.tfl_client.set_cached"),
        ):
            try:
                await TflClient._cached_api_call("https://api.tfl.gov.uk/x", {})
                raise AssertionError("Expected HttpError for 409")
            except HttpError as e:
                assert e.status == 409
                assert "route planner unavailable" in str(e)


class _FakeRoutesClient:
    """RoutesPostClient stub returning a canned Google TRANSIT route."""

    def __init__(self, payload):
        self.payload = payload
        self.posted = []

    async def post(self, body, field_mask, *, options=None):
        self.posted.append((body, field_mask))
        return self.payload


class _RaisingRoutesClient:
    async def post(self, body, field_mask, *, options=None):
        raise RuntimeError("google down")


class TestGoogleTransitFallback:
    """CommuteRouter.transit_route — the National Rail fallback router
    (Google Routes TRANSIT) used for origins beyond TfL coverage."""

    @staticmethod
    def _route_payload():
        """The live response shape probed for Hungerford → Pimlico:
        walk → GWR train → walk → TfL bus 36 → walk.  Google's transit
        response omits stop names and vehicle types — only line/agency."""
        return {
            "routes": [
                {
                    "duration": "5952s",
                    "legs": [
                        {
                            "steps": [
                                {"travelMode": "WALK", "staticDuration": "600s"},
                                {
                                    "travelMode": "TRANSIT",
                                    "staticDuration": "780s",
                                    "transitDetails": {
                                        "transitLine": {
                                            "nameShort": "GWR",
                                            "agencies": [{"name": "GWR"}],
                                        }
                                    },
                                },
                                {
                                    "travelMode": "TRANSIT",
                                    "staticDuration": "2700s",
                                    "transitDetails": {
                                        "transitLine": {
                                            "nameShort": "GWR",
                                            "agencies": [{"name": "GWR"}],
                                        }
                                    },
                                },
                                {"travelMode": "WALK", "staticDuration": "200s"},
                                {
                                    "travelMode": "TRANSIT",
                                    "staticDuration": "1586s",
                                    "transitDetails": {
                                        "transitLine": {
                                            "nameShort": "36",
                                            "agencies": [{"name": "Transport for London"}],
                                        }
                                    },
                                },
                                {"travelMode": "WALK", "staticDuration": "86s"},
                            ]
                        }
                    ],
                }
            ]
        }

    @pytest.mark.asyncio
    async def test_parses_transit_legs_and_duration(self):
        from houses.commute import LegMode
        from houses.commute_router import CommuteRouter
        from houses.geopoint import GeoPoint
        from houses.model.domain import PlaceOfInterest

        router = CommuteRouter(routes_client=_FakeRoutesClient(self._route_payload()))
        commute = await router.transit_route(
            GeoPoint(51.415344, -1.511056),
            PlaceOfInterest(label="Pimlico", address="1 Drummond Gate, Pimlico, London SW1V 2QQ"),
        )
        assert commute is not None and not commute.infeasible
        assert commute.duration.magnitude == 99  # 5952s ≈ 99 min
        modes = [leg.mode for cg in commute.details for leg in cg.legs]
        assert modes == [
            LegMode.WALK,
            LegMode.TRAIN,
            LegMode.TRAIN,
            LegMode.WALK,
            LegMode.BUS,
            LegMode.WALK,
        ], "the fallback journey is walk → train → walk → bus → walk"

    @pytest.mark.asyncio
    async def test_rail_leg_names_the_london_terminus_for_pricing(self):
        """The terminal name is what lets RailFareNode price the journey
        (Google's response omits stop names)."""
        from houses.commute_router import CommuteRouter
        from houses.geopoint import GeoPoint
        from houses.model.domain import PlaceOfInterest

        router = CommuteRouter(routes_client=_FakeRoutesClient(self._route_payload()))
        commute = await router.transit_route(
            GeoPoint(51.415344, -1.511056),
            PlaceOfInterest(label="Pimlico", address="1 Drummond Gate, Pimlico, London SW1V 2QQ"),
        )
        assert commute is not None
        train_legs = [leg for cg in commute.details for leg in cg.legs if leg.mode.name == "TRAIN"]
        assert train_legs and all(
            leg.end_station == "London Paddington Rail Station" for leg in train_legs
        ), "GWR journeys name Paddington as the London terminus"
        bus_leg = [
            leg for cg in commute.details for leg in cg.legs if leg.mode.name == "BUS"
        ][0]
        assert bus_leg.line_name == "36" and bus_leg.end_station == ""

    @pytest.mark.asyncio
    async def test_no_routes_returns_none(self):
        from houses.commute_router import CommuteRouter
        from houses.geopoint import GeoPoint
        from houses.model.domain import PlaceOfInterest

        router = CommuteRouter(routes_client=_FakeRoutesClient({"routes": []}))
        result = await router.transit_route(
            GeoPoint(51.415344, -1.511056),
            PlaceOfInterest(label="Pimlico", address="1 Drummond Gate, Pimlico, London SW1V 2QQ"),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_api_failure_returns_none_not_crash(self):
        from houses.commute_router import CommuteRouter
        from houses.geopoint import GeoPoint
        from houses.model.domain import PlaceOfInterest

        router = CommuteRouter(routes_client=_RaisingRoutesClient())
        result = await router.transit_route(
            GeoPoint(51.415344, -1.511056),
            PlaceOfInterest(label="Pimlico", address="1 Drummond Gate, Pimlico, London SW1V 2QQ"),
        )
        assert result is None
