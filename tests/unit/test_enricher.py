"""Tests for enrichment logic."""

import copy
from datetime import datetime
from unittest.mock import patch

import pytest
from httpx import AsyncClient, MockTransport, Response

from houses.enricher import (
    _END_PC_RE,
    _OUTCODE_RE,
    FEE_PAYING_TYPES,
    _apply_park_and_ride_to_journeys,
    _boys_eligible,
    _compute_petrol_from_distance_km,
    _format_route_summary,
    _haversine_km,
    _next_weekday_date_params,
    _phase_filter,
    _pick_best_journey,
    _school_coords,
    _school_to_info,
    _shorten_station,
    compute_commute_breakdown,
)
from houses.models import PetrolCost, TransitInfo


class TestOutcodeDetection:
    def test_outcode_matches_sl6(self):
        assert _OUTCODE_RE.match("SL6")

    def test_outcode_matches_sw1e(self):
        assert _OUTCODE_RE.match("SW1E")

    def test_outcode_matches_ec3a(self):
        assert _OUTCODE_RE.match("EC3A")

    def test_full_postcode_does_not_match(self):
        assert not _OUTCODE_RE.match("RG14 1AA")

    def test_full_postcode_no_space_does_not_match(self):
        assert not _OUTCODE_RE.match("RG141AA")

    def test_empty_does_not_match(self):
        assert not _OUTCODE_RE.match("")

    def test_not_a_postcode_does_not_match(self):
        assert not _OUTCODE_RE.match("not a postcode")


class TestBoysEligible:
    def test_mixed_gender_eligible(self):
        assert _boys_eligible({"Gender (name)": "Mixed", "TypeOfEstablishment (name)": "Community School"})

    def test_boys_gender_eligible(self):
        assert _boys_eligible({"Gender (name)": "Boys", "TypeOfEstablishment (name)": "Academy Converter"})

    def test_girls_gender_ineligible(self):
        assert not _boys_eligible({"Gender (name)": "Girls", "TypeOfEstablishment (name)": "Community School"})

    def test_independent_school_ineligible(self):
        assert not _boys_eligible({"Gender (name)": "Mixed", "TypeOfEstablishment (name)": "Independent School"})

    def test_missing_fields_returns_false(self):
        assert not _boys_eligible({})


class TestHaversine:
    def test_same_point_returns_zero(self):
        # Same lat/lng should be 0 km
        dist = _haversine_km(51.5, -0.13, 51.5, -0.13)
        assert dist == 0.0

    def test_known_distance(self):
        # London to Brighton ~75km
        dist = _haversine_km(51.5, -0.13, 50.83, -0.14)
        assert 70 < dist < 80

    def test_symmetric(self):
        d1 = _haversine_km(51.5, -0.13, 52.0, 0.0)
        d2 = _haversine_km(52.0, 0.0, 51.5, -0.13)
        assert abs(d1 - d2) < 0.001


class TestCommuteBreakdown:
    @pytest.mark.asyncio
    async def test_yearly_formula_with_all_costs(self):
        """46wk x (10 + 15 + 2*24) = 46 x 73 = 3358"""
        simon = TransitInfo(destination_label="S", destination_postcode="SW1V 2QQ", daily_cost_gbp=15.0)
        lorena = TransitInfo(destination_label="L", destination_postcode="EC3A 7LP", daily_cost_gbp=24.0)
        petrol = PetrolCost(cost_gbp=10.0)
        result = await compute_commute_breakdown(simon, lorena, petrol)
        assert result.simon_daily_gbp == 15.0
        assert result.lorena_daily_gbp == 24.0
        assert result.bracknell_daily_gbp == 10.0
        assert result.yearly_total_gbp == 3358.0
        assert "46" in result.formula_explanation

    @pytest.mark.asyncio
    async def test_missing_costs_returns_none(self):
        simon = TransitInfo(destination_label="S", destination_postcode="SW1V 2QQ", daily_cost_gbp=None)
        lorena = TransitInfo(destination_label="L", destination_postcode="EC3A 7LP", daily_cost_gbp=24.0)
        petrol = PetrolCost(cost_gbp=10.0)
        result = await compute_commute_breakdown(simon, lorena, petrol)
        assert result.yearly_total_gbp is None


class TestFeePayingTypes:
    def test_includes_known_private_types(self):
        assert "independent school" in FEE_PAYING_TYPES
        assert "other independent school" in FEE_PAYING_TYPES

    def test_excludes_public_types(self):
        assert "community school" not in FEE_PAYING_TYPES
        assert "academy converter" not in FEE_PAYING_TYPES


class TestPetrolCalculation:
    """_compute_petrol_from_distance_km — pure math, no mocks needed."""

    def test_known_distance(self):
        """100 km round trip at 45mpg, £1.45/L.

        Litres per 100km = 235.214 / 45 = 5.227...
        Litres used = (100 / 100) * 5.227 = 5.227
        Cost = 5.227 * 1.45 = 7.58
        """
        cost = _compute_petrol_from_distance_km(100.0)
        assert cost == 7.58

    def test_zero_distance(self):
        """Zero km round trip should cost £0.00."""
        cost = _compute_petrol_from_distance_km(0.0)
        assert cost == 0.0

    def test_short_trip(self):
        """10 km round trip should be a fraction of the 100 km cost."""
        cost_100 = _compute_petrol_from_distance_km(100.0)
        cost_10 = _compute_petrol_from_distance_km(10.0)
        # 10x the 10km cost should approximately equal the 100km cost
        # (small difference due to rounding at each step)
        assert abs(cost_10 * 10 - cost_100) < 0.05

    def test_50km_round_trip(self):
        """50 km round trip at 45mpg, £1.45/L.

        Litres per 100km = 235.214 / 45 = 5.227...
        Litres used = (50 / 100) * 5.227 = 2.613...
        Cost = 2.613 * 1.45 = 3.79
        """
        cost = _compute_petrol_from_distance_km(50.0)
        assert cost == 3.79

    def test_result_is_rounded_to_2dp(self):
        """Cost should always be rounded to 2 decimal places."""
        cost = _compute_petrol_from_distance_km(37.5)
        assert cost == round(cost, 2)


class TestPhaseFilter:
    """_phase_filter — checks a school's PhaseOfEducation matches the target."""

    def test_primary_phase_matches_primary(self):
        assert _phase_filter({"PhaseOfEducation (name)": "Primary"}, "primary")

    def test_secondary_phase_matches_secondary(self):
        assert _phase_filter({"PhaseOfEducation (name)": "Secondary"}, "secondary")

    def test_primary_does_not_match_secondary(self):
        assert not _phase_filter({"PhaseOfEducation (name)": "Primary"}, "secondary")

    def test_not_available_does_not_match(self):
        assert not _phase_filter({"PhaseOfEducation (name)": "Not applicable"}, "primary")

    def test_missing_phase_does_not_match(self):
        assert not _phase_filter({}, "primary")

    def test_case_insensitive_school_data(self):
        """School's phase value is lowercased before matching; target is used as-is."""
        assert _phase_filter({"PhaseOfEducation (name)": "PRIMARY"}, "primary")
        assert _phase_filter({"PhaseOfEducation (name)": "Secondary"}, "secondary")

    def test_phase_contains_substring(self):
        """_phase_filter uses 'in' for substring matching on phase."""
        assert _phase_filter({"PhaseOfEducation (name)": "Primary"}, "prim")
        assert not _phase_filter({"PhaseOfEducation (name)": "Not applicable"}, "primary")


class TestSchoolCoords:
    """_school_coords — parses Latitude/Longitude from a school row dict."""

    def test_valid_coords(self):
        coords = _school_coords({"Latitude": "51.5", "Longitude": "-0.13"})
        assert coords == (51.5, -0.13)

    def test_missing_lat_returns_none(self):
        assert _school_coords({"Longitude": "-0.13"}) is None

    def test_missing_lng_returns_none(self):
        assert _school_coords({"Latitude": "51.5"}) is None

    def test_empty_strings_returns_none(self):
        assert _school_coords({"Latitude": "", "Longitude": ""}) is None

    def test_zero_coords(self):
        """Zero lat/lng should still return (0.0, 0.0) since "0" is truthy."""
        coords = _school_coords({"Latitude": "0", "Longitude": "0"})
        assert coords == (0.0, 0.0)

    def test_returns_floats(self):
        coords = _school_coords({"Latitude": "52.2053", "Longitude": "0.1218"})
        assert coords == (52.2053, 0.1218)
        assert isinstance(coords[0], float)
        assert isinstance(coords[1], float)


class TestSchoolToInfo:
    """_school_to_info — converts a school dict (from CSV row) to a SchoolInfo object."""

    def test_basic_conversion(self):
        school = {
            "EstablishmentName": "Test Primary School",
            "Gender (name)": "Mixed",
            "TypeOfEstablishment (name)": "Community School",
            "URN": "123456",
            "SchoolWebsite": "https://example.com",
            "OfstedRating (name)": "Good",
            "InspectionYear": "2023",
        }
        info = _school_to_info(school, dist_km=0.8, school_type="primary")

        assert info.name == "Test Primary School"
        assert info.type == "primary"
        assert info.distance_km == 0.8
        assert info.gender == "mixed"
        assert info.fee_paying is False
        assert info.urn == "123456"
        assert info.website == "https://example.com"
        assert info.ofsted_rating == "Good"
        assert info.inspection_year == "2023"

    def test_walk_time_from_distance(self):
        """Walk time should be distance / 5 * 60 (5 km/h walking speed)."""
        info = _school_to_info({}, dist_km=1.0, school_type="primary")
        assert info.walking_time_minutes == 12  # 1.0 / 5 * 60 = 12

    def test_zero_distance_walk_time(self):
        """Zero distance should yield None walk time."""
        info = _school_to_info({}, dist_km=0.0, school_type="primary")
        assert info.walking_time_minutes is None

    def test_none_distance(self):
        """None distance should yield None walk time and None distance_km."""
        info = _school_to_info({}, dist_km=None, school_type="primary")
        assert info.walking_time_minutes is None
        assert info.distance_km is None

    def test_distance_rounded_to_2dp(self):
        info = _school_to_info({}, dist_km=1.23456, school_type="primary")
        assert info.distance_km == 1.23

    def test_missing_name_defaults(self):
        info = _school_to_info({}, dist_km=None, school_type="secondary")
        assert info.name == "Unknown"
        assert info.urn == ""

    def test_type_preserved(self):
        info = _school_to_info({}, dist_km=0.5, school_type="secondary")
        assert info.type == "secondary"


class TestEndPostcodePattern:
    """_END_PC_RE — strips trailing postcodes from address strings."""

    def test_full_postcode(self):
        assert _END_PC_RE.search(", RG14 1AA")

    def test_outcode(self):
        assert _END_PC_RE.search(", SL6")

    def test_london_outcode(self):
        assert _END_PC_RE.search(", SW1E")

    def test_no_postcode(self):
        assert not _END_PC_RE.search("Some Road, Town")

    def test_empty_string(self):
        assert not _END_PC_RE.search("")

    def test_postcode_without_comma(self):
        """Pattern requires a leading comma — a bare postcode shouldn't match."""
        assert not _END_PC_RE.search("RG14 1AA")


class TestNextWeekdayDateParams:
    """_next_weekday_date_params — date/time for next weekday 09:00."""

    def test_returns_weekday_date(self):
        result = _next_weekday_date_params()
        assert "date" in result
        assert "time" in result
        assert result["time"] == "0900"
        dt = datetime.strptime(result["date"], "%Y%m%d")
        assert dt.weekday() < 5, f"{result['date']} is not a weekday"


class TestShortenStation:
    """_shorten_station — strip common station suffixes."""

    def test_rail_station(self):
        assert _shorten_station("Maidenhead Rail Station") == "Maidenhead"

    def test_underground_station(self):
        assert _shorten_station("Paddington Underground Station") == "Paddington"

    def test_generic_station(self):
        assert _shorten_station("Oxford Circus Station") == "Oxford Circus"

    def test_no_suffix(self):
        assert _shorten_station("Some Street, Town") == "Some Street, Town"

    def test_strips_london_prefix(self):
        assert _shorten_station("London Paddington Rail Station") == "Paddington"
        assert _shorten_station("London Waterloo Rail Station") == "Waterloo"

    def test_empty_string(self):
        assert _shorten_station("") == ""


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
        result = _format_route_summary(self.TFL_JOURNEY)
        # First walk is to a non-station → no destination
        assert "walk 6m" in result
        # Middle walk to a station → shows destination
        assert "walk to Maidenhead (5m)" in result
        # Last walk is final destination → no destination
        assert "walk 7m" in result

    def test_walking_shows_destination_for_stations(self):
        """Walking segments show their destination when walking to a station
        rather than the final property."""
        result = _format_route_summary(self.TFL_JOURNEY)
        # The second walking leg arrives at Maidenhead Rail Station
        assert "walk to Maidenhead (5m)" in result

    def test_includes_transit_legs(self):
        result = _format_route_summary(self.TFL_JOURNEY)
        assert "bus(7) to Maidenhead" in result
        assert "Train to Paddington (20m)" in result
        assert "Bakerloo line to Oxford Circus (8m)" in result

    def test_includes_station_names_for_transit_legs(self):
        result = _format_route_summary(self.TFL_JOURNEY)
        assert "Train to Paddington (20m)" in result
        assert "Bakerloo line to Oxford Circus (8m)" in result

    def test_omits_departure_when_same_as_previous_arrival(self):
        """Transit leg's departure is omitted when it matches the previous transit leg's arrival."""
        result = _format_route_summary(self.TFL_JOURNEY)
        assert "Train to Paddington (20m)" in result
        assert "Bakerloo line to Oxford Circus (8m)" in result

    def test_handles_london_prefix_mismatch(self):
        """NR arrives at 'London X' — 'London ' prefix is stripped."""
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
        result = _format_route_summary(self.TFL_JOURNEY)
        assert "SL6 3YZ" not in result
        assert "Pimlico" not in result  # walking leg at end has Pimlico, but should be omitted

    def test_duration_numbers_appear(self):
        result = _format_route_summary(self.TFL_JOURNEY)
        assert "6m" in result
        assert "20m" in result
        assert "8m" in result

    def test_empty_legs(self):
        result = _format_route_summary({"legs": []})
        assert result == ""

    def test_no_legs_key(self):
        result = _format_route_summary({})
        assert result == ""

    def test_driving_leg_format(self):
        """Park-and-ride replaces the first walk leg with a drive leg."""
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


class TestPickBestJourney:
    """_pick_best_journey — shortest journey selection."""

    def test_returns_duration_and_cost_and_route(self):
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
        data = {
            "journeys": [
                {"duration": 50, "fare": {"totalCost": 1200}, "legs": []},
                {"duration": 30, "fare": {"totalCost": 800}, "legs": []},
            ]
        }
        duration, cost, _ = _pick_best_journey(data)
        assert duration == 30
        assert cost == 16.00  # 800 / 100 * 2

    def test_empty_journeys(self):
        duration, cost, route = _pick_best_journey({"journeys": []})
        assert duration is None
        assert cost is None
        assert route == ""

    def test_none_data(self):
        duration, cost, route = _pick_best_journey(None)
        assert duration is None
        assert cost is None
        assert route == ""

    def test_route_summary_from_best_journey(self):
        """Route summary describes the best (shortest) journey, not the first."""
        walk_leg = {"mode": {"name": "walking"}, "duration": 90, "instruction": {"summary": ""}}
        train_leg = {
            "mode": {"name": "national-rail"},
            "duration": 20,
            "instruction": {"summary": "TrainCo to London"},
            "departurePoint": {"commonName": "Town Station"},
            "arrivalPoint": {"commonName": "London Station"},
        }
        data = {
            "journeys": [
                {"duration": 90, "legs": [walk_leg]},
                {"duration": 45, "legs": [train_leg]},
            ]
        }
        _, _, route = _pick_best_journey(data)
        assert "Train to London" in route
        assert "walk" not in route


class TestStationLookup:
    """_lookup_station_coords — find station coords from CSV by name."""

    def test_finds_didcot_parkway(self):
        """'Didcot Parkway Rail Station' should match 'Didcot Parkway' in stations.csv."""
        from houses.enricher import _lookup_station_coords

        coords = _lookup_station_coords("Didcot Parkway Rail Station")
        assert coords is not None
        # Didcot Parkway is at ~51.611, -1.243 in stations.csv
        assert abs(coords[0] - 51.611) < 0.02
        assert abs(coords[1] + 1.243) < 0.02

    def test_returns_none_for_unknown(self):
        from houses.enricher import _lookup_station_coords

        assert _lookup_station_coords("Some Fake Station") is None

    def test_strips_station_suffixes(self):
        from houses.enricher import _lookup_station_coords

        # Should find Maidenhead in stations.csv (not "Maidenhead Rail Station")
        coords = _lookup_station_coords("Maidenhead Rail Station")
        assert coords is not None


class TestGetDriveMinutes:
    """_get_drive_minutes — driving duration using stations.csv coords."""

    @pytest.mark.asyncio
    async def test_didcot_drive_is_reasonable(self):
        """OX11 8QP is ~1km from Didcot Parkway — drive should be <30 min."""
        from houses.enricher import _get_drive_minutes

        def handler(request):
            url = str(request.url)
            if "api.postcodes.io" in url:
                return Response(
                    200,
                    json={"status": 200, "result": {"latitude": 51.603, "longitude": -1.254}},
                )
            if "openrouteservice.org/v2/directions" in url:
                return Response(
                    200,
                    json={"routes": [{"summary": {"distance": 1.5, "duration": 180}}]},  # 3 min
                )
            return Response(404)

        original_init = AsyncClient.__init__

        def patched_init(self, **kwargs):
            kwargs["transport"] = MockTransport(handler)
            original_init(self, **kwargs)

        with patch.object(AsyncClient, "__init__", patched_init):
            result = await _get_drive_minutes("OX11 8QP", "Didcot Parkway Rail Station")

        assert result is not None, "Should have found a drive time"
        assert result < 30, f"Expected <30 min for nearby station, got {result}"


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
            },
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
            },
        ]
    }

    @pytest.mark.asyncio
    async def test_replaces_long_walk_with_drive(self):
        data = copy.deepcopy(self.LONG_WALK_DATA)
        with patch("houses.enricher._get_drive_minutes", return_value=10):
            result = await _apply_park_and_ride_to_journeys(
                data,
                "SL6 3YZ",
                max_walk_minutes=20,
            )
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "driving"
        assert legs[0]["duration"] == 10
        assert result["journeys"][0]["duration"] == 62  # 87 - 35 + 10

    @pytest.mark.asyncio
    async def test_skips_short_walk(self):
        data = copy.deepcopy(self.SHORT_WALK_DATA)
        with patch("houses.enricher._get_drive_minutes", return_value=3):
            result = await _apply_park_and_ride_to_journeys(
                data,
                "KT13 0TD",
                max_walk_minutes=20,
            )
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "walking"
        assert legs[0]["duration"] == 10  # unchanged

    @pytest.mark.asyncio
    async def test_skips_non_walking_first_leg(self):
        """When first leg is already a train, park-and-ride does nothing."""
        data = {
            "journeys": [
                {
                    "duration": 45,
                    "legs": [
                        {
                            "mode": {"name": "national-rail"},
                            "duration": 20,
                            "arrivalPoint": {"commonName": "London Paddington Rail Station"},
                            "instruction": {"summary": "GWR to Paddington"},
                        },
                    ],
                },
            ]
        }
        with patch("houses.enricher._get_drive_minutes") as mock_drive:
            result = await _apply_park_and_ride_to_journeys(data, "SL6", 20)
        mock_drive.assert_not_called()
        assert result["journeys"][0]["legs"][0]["mode"]["name"] == "national-rail"

    @pytest.mark.asyncio
    async def test_skips_when_drive_lookup_fails(self):
        data = copy.deepcopy(self.LONG_WALK_DATA)
        with patch("houses.enricher._get_drive_minutes", return_value=None):
            result = await _apply_park_and_ride_to_journeys(
                data,
                "SL6 3YZ",
                max_walk_minutes=20,
            )
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "walking"  # unchanged
        assert legs[0]["duration"] == 35  # unchanged

    @pytest.mark.asyncio
    async def test_format_includes_drive_in_route_after_park_and_ride(self):
        """After park-and-ride, _format_route_summary shows Drive to ..."""
        data = copy.deepcopy(self.LONG_WALK_DATA)
        with patch("houses.enricher._get_drive_minutes", return_value=10):
            result = await _apply_park_and_ride_to_journeys(
                data,
                "SL6 3YZ",
                max_walk_minutes=20,
            )
        best = min(result["journeys"], key=lambda j: j.get("duration", 9999))
        summary = _format_route_summary(best)
        assert "Drive to Maidenhead (10m)" in summary
        assert "Train to Paddington (20m)" in summary
        assert "walk 7m" in summary  # final walk unchanged
