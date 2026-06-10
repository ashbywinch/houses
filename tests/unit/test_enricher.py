"""Tests for enrichment logic."""

from datetime import datetime
from pathlib import Path

import pytest

from houses.enricher import (
    _END_PC_RE,
    _OUTCODE_RE,
    FEE_PAYING_TYPES,
    _boys_eligible,
    _compute_petrol_from_distance_km,
    _format_route_summary,
    _next_weekday_date_params,
    _phase_filter,
    _pick_best_journey,
    _school_coords,
    _school_to_info,
    _shorten_station,
    compute_commute_breakdown,
)
from houses.geo import GeoPoint
from houses.models import PetrolCost, TransitInfo

FIXTURES_DIR = Path("tests/fixtures/parking_tariffs")


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
        dist = GeoPoint(51.5, -0.13).distance_km_to(GeoPoint(51.5, -0.13))
        assert dist == 0.0

    def test_known_distance(self):
        # London to Brighton ~75km
        dist = GeoPoint(51.5, -0.13).distance_km_to(GeoPoint(50.83, -0.14))
        assert 70 < dist < 80

    def test_symmetric(self):
        d1 = GeoPoint(51.5, -0.13).distance_km_to(GeoPoint(52.0, 0.0))
        d2 = GeoPoint(52.0, 0.0).distance_km_to(GeoPoint(51.5, -0.13))
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


class TestCleanStationNameForMatching:
    """_clean_station_name_for_matching — strip suffixes but NOT London prefix."""

    def test_strips_rail_station(self):
        from houses.enricher import _clean_station_name_for_matching

        assert _clean_station_name_for_matching("Woking Rail Station") == "Woking"

    def test_strips_underground_station(self):
        from houses.enricher import _clean_station_name_for_matching

        assert _clean_station_name_for_matching("Paddington Underground Station") == "Paddington"

    def test_strips_generic_station(self):
        from houses.enricher import _clean_station_name_for_matching

        assert _clean_station_name_for_matching("Oxford Circus Station") == "Oxford Circus"

    def test_keeps_london_prefix(self):
        from houses.enricher import _clean_station_name_for_matching

        assert _clean_station_name_for_matching("London Paddington Rail Station") == "London Paddington"

    def test_no_suffix(self):
        from houses.enricher import _clean_station_name_for_matching

        assert _clean_station_name_for_matching("Some Street, Town") == "Some Street, Town"


class TestStationCrsLookup:
    """_lookup_station_crs — find CRS from stations.csv by exact match."""

    def test_finds_woking(self):
        from houses.enricher import _lookup_station_crs

        crs = _lookup_station_crs("Woking Rail Station")
        assert crs == "WOK"

    def test_finds_maidenhead(self):
        from houses.enricher import _lookup_station_crs

        crs = _lookup_station_crs("Maidenhead Rail Station")
        assert crs == "MAI"

    def test_case_insensitive(self):
        from houses.enricher import _lookup_station_crs

        crs = _lookup_station_crs("woking rail station")
        assert crs == "WOK"

    def test_not_found_returns_none(self):
        from houses.enricher import _lookup_station_crs

        crs = _lookup_station_crs("Some Fake Station")
        assert crs is None


class TestBusFareDailyCost:
    """_compute_bus_daily_cost — cheapest product covering two journeys."""

    def test_uses_return_when_cheaper_than_2x_single(self):
        from houses.enricher import _compute_bus_daily_cost

        # adult_return £4.00 vs 2×single £5.00 → £4.00
        cost = _compute_bus_daily_cost({"adult_single": 2.50, "adult_return": 4.00})
        assert cost == 4.00

    def test_uses_day_rider_when_cheapest(self):
        from houses.enricher import _compute_bus_daily_cost

        # adult_day £4.50 < 2×single £5.00 → £4.50
        cost = _compute_bus_daily_cost({"adult_single": 2.50, "adult_day": 4.50})
        assert cost == 4.50

    def test_uses_2x_single_when_no_other_products(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 2.50})
        assert cost == 5.00

    def test_national_cap_applied_to_single(self):
        from houses.enricher import _compute_bus_daily_cost

        meta = {"national_max_single_gbp": 3.00}
        # BODS single is £4.00, cap → £3.00, daily → £6.00
        cost = _compute_bus_daily_cost({"adult_single": 4.00}, meta)
        assert cost == 6.00

    def test_national_cap_below_cap(self):
        from houses.enricher import _compute_bus_daily_cost

        meta = {"national_max_single_gbp": 3.00}
        # BODS single £2.50 is below cap → used as-is
        cost = _compute_bus_daily_cost({"adult_single": 2.50}, meta)
        assert cost == 5.00

    def test_national_cap_not_set(self):
        from houses.enricher import _compute_bus_daily_cost

        # meta is None → BODS single used as-is
        cost = _compute_bus_daily_cost({"adult_single": 4.00})
        assert cost == 8.00


class TestBusFareLookup:
    """_lookup_bus_roundtrip_cost — stop name → zone → zone pair → price."""

    def test_randolph_close_to_woking_station(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost("randolph close", "woking railway station")
        assert cost == 1.8

    def test_case_insensitive_matching(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost("RANDOLPH CLOSE", "WOKING RAILWAY STATION")
        assert cost == 1.8

    def test_tfl_area_prefix_dep_match(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost("Knaphill, Randolph Close", "Woking, Woking Railway Station")
        assert cost == 1.8

    def test_tfl_westfield_not_in_zone_fares(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost("Westfield, Westfield Common", "Woking, Woking Railway Station")
        assert cost is None, "Westfield->Woking has no zone pair in BODS data (data gap)"

    def test_tfl_brookwood_to_woking(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost("Brookwood, Brookwood Railway Station", "Woking, Woking Railway Station")
        assert cost == 3.0

    def test_fuzzy_match_periods(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost("St. Johns, St. James Close", "Woking, Woking Railway Station")
        assert cost is not None

    def test_fuzzy_match_does_not_match_unrelated(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost("Knaphill, Supermarket Car Park", "Woking, Woking Railway Station")
        assert cost is None, "Should not match unrelated stop 'supermarket car park'"

        cost2 = _lookup_bus_roundtrip_cost("North London Bus Stop", "Woking, Woking Railway Station")
        assert cost2 is None, "Should not match stop in entirely different area"

    def test_fuzzy_match_short_noise_words_rejected(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost("Woking Station", "Woking, Woking Railway Station")
        assert cost is None, "'Woking Station' should not match 'station' (a different stop)"

    def test_unknown_stops_return_none(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost("Unknown Stop", "Another Unknown")
        assert cost is None

    def test_same_stop_is_not_free(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost("randolph close", "randolph close")
        assert cost is not None

    def test_reversed_direction(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost("woking railway station", "randolph close")
        assert cost == 1.8

    def test_coord_fallback_without_coords_still_returns_none(self):
        from houses.enricher import _lookup_bus_roundtrip_cost

        cost = _lookup_bus_roundtrip_cost(
            "Unknown Stop",
            "Another Unknown",
            {"lat": 51.3, "lon": -0.5},
            {"lat": 51.31, "lon": -0.49},
        )
        assert cost is None


class TestComputeBusDailyCost:
    """_compute_bus_daily_cost — cheapest product selection."""

    def test_single_only_doubled(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 1.5})
        assert cost == 3.0

    def test_single_with_return(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 1.5, "adult_return": 2.5})
        assert cost == 2.5

    def test_single_with_day_cheaper(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 2.0, "adult_day": 3.5})
        assert cost == 3.5

    def test_day_more_expensive_than_double(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 0.9, "adult_day": 8.5})
        assert cost == 1.8

    def test_national_cap_applied_before_doubling(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 3.5}, {"national_max_single_gbp": 3.0})
        assert cost == 6.0

    def test_return_cheaper_than_capped_double(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 2.0, "adult_return": 3.8}, {"national_max_single_gbp": 3.0})
        assert cost == 3.8

    def test_no_single_returns_zero(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({})
        assert cost == 0.0

    def test_empty_fares_returns_zero(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({})
        assert cost == 0.0

    def test_return_more_expensive_than_single_double(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 1.0, "adult_return": 2.5})
        assert cost == 2.0

    def test_cap_makes_singles_cheaper_than_return(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 3.5, "adult_return": 5.0}, {"national_max_single_gbp": 2.0})
        assert cost == 4.0

    def test_return_only_no_single(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_return": 4.0})
        assert cost == 4.0

    def test_day_only_no_single_or_return(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_day": 5.0})
        assert cost == 5.0

    def test_all_products_day_is_cheapest(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 2.0, "adult_return": 3.5, "adult_day": 3.0})
        assert cost == 3.0

    def test_all_products_return_is_cheapest(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 2.0, "adult_return": 2.5, "adult_day": 6.0})
        assert cost == 2.5

    def test_all_products_singles_cheapest_even_with_cap(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_single": 1.0, "adult_return": 3.0, "adult_day": 4.0})
        assert cost == 2.0


class TestStopToZoneMapping:
    """Zone lookup for stop names from the data file."""

    def test_randolph_close_maps_to_zone(self):
        from houses.enricher import _load_bus_fares

        data = _load_bus_fares()
        scso = data.get("Stagecoach_South", {})
        zone = scso.get("stop_zones", {}).get("randolph close")
        assert zone is not None

    def test_woking_station_maps_to_same_zone(self):
        from houses.enricher import _load_bus_fares

        data = _load_bus_fares()
        scso = data.get("Stagecoach_South", {})
        assert scso.get("stop_zones", {}).get("randolph close") == scso.get("stop_zones", {}).get(
            "woking railway station"
        )


class TestZonePairLookup:
    """Zone pair -> fare products."""

    def test_randolph_woking_station_has_single(self):
        from houses.enricher import _load_bus_fares

        data = _load_bus_fares()
        scso = data.get("Stagecoach_South", {})
        sz = scso.get("stop_zones", {})
        dep_zone = sz.get("randolph close")
        arr_zone = sz.get("woking railway station")
        assert dep_zone is not None
        assert arr_zone is not None
        fares = scso.get("zone_fares", {}).get(f"{dep_zone}:{arr_zone}")
        assert fares is not None
        assert fares.get("adult_single") == 0.9

    def test_randolph_woking_has_adult_day(self):
        from houses.enricher import _load_bus_fares

        data = _load_bus_fares()
        scso = data.get("Stagecoach_South", {})
        sz = scso.get("stop_zones", {})
        dep_zone = sz.get("randolph close")
        arr_zone = sz.get("woking railway station")
        fares = scso.get("zone_fares", {}).get(f"{dep_zone}:{arr_zone}")
        assert fares is not None
        assert fares.get("adult_day") == 8.5

    def test_reverse_zone_pair_has_same_fares(self):
        from houses.enricher import _load_bus_fares

        data = _load_bus_fares()
        scso = data.get("Stagecoach_South", {})
        fares_fwd = scso.get("zone_fares", {}).get("zone@510@17@boarding:zone@510@17@boarding", {})
        assert fares_fwd.get("adult_single") == 0.9


class TestPickBestLorenaRoute:
    """_pick_best_lorena_route — bus vs no-bus decision."""

    def test_uses_bus_when_much_faster(self):
        from houses.enricher import _pick_best_lorena_route
        from houses.models import TransitInfo

        no_bus = TransitInfo(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=50)
        with_bus = TransitInfo(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=30)
        result = _pick_best_lorena_route(no_bus, with_bus)
        assert result == with_bus

    def test_rejects_bus_when_not_faster(self):
        from houses.enricher import _pick_best_lorena_route
        from houses.models import TransitInfo

        no_bus = TransitInfo(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=35)
        with_bus = TransitInfo(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=33)
        result = _pick_best_lorena_route(no_bus, with_bus)
        assert result == no_bus

    def test_falls_back_to_no_bus_when_with_bus_none(self):
        from houses.enricher import _pick_best_lorena_route
        from houses.models import TransitInfo

        no_bus = TransitInfo(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=50)
        with_bus = TransitInfo(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=None)
        result = _pick_best_lorena_route(no_bus, with_bus)
        assert result == no_bus

    def test_falls_back_to_with_bus_when_no_bus_none(self):
        from houses.enricher import _pick_best_lorena_route
        from houses.models import TransitInfo

        no_bus = TransitInfo(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=None)
        with_bus = TransitInfo(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=30)
        result = _pick_best_lorena_route(no_bus, with_bus)
        assert result == with_bus


class TestGoogleRouteFallback:
    """compute_lorena_commute falls back to Google when TfL has no bus."""

    @pytest.mark.asyncio
    async def test_route_summary_preserves_timing_brackets(self, monkeypatch):
        from houses.enricher import compute_lorena_commute
        from houses.models import TransitInfo

        no_bus = TransitInfo(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=116,
            daily_cost_gbp=None,
            route_summary="walk to Fleet (46m) → Train to Waterloo (42m) → Tube to Bank (4m) → walk (18m)",
        )
        with_bus = TransitInfo(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=116,
            daily_cost_gbp=None,
            route_summary="walk to Fleet (46m) → Train to Waterloo (42m) → Tube to Bank (4m) → walk (18m)",
        )
        google_bus = TransitInfo(
            destination_label="L (Google)",
            destination_postcode="EC3A 7LP",
            duration_minutes=55,
            daily_cost_gbp=3.8,
            route_summary="bus to Fleet → Train to Waterloo",
            bus_cost_gbp=3.8,
        )

        async def mock_transit(*_a, **_kw):
            return with_bus if _kw.get("allow_bus") else no_bus

        async def mock_google(*_):
            return google_bus

        monkeypatch.setattr("houses.enricher.compute_transit", mock_transit)
        monkeypatch.setattr("houses.enricher._compute_google_transit", mock_google)

        result = await compute_lorena_commute("GU52")
        assert result.bus_cost_gbp is not None
        route = result.route_summary
        assert "(46m)" not in route, "Should not include old walk duration"
        assert "(" in route, f"Route should preserve timing brackets: {route}"
        assert "42m" in route, f"Should preserve train timing: {route}"
        assert "4m" in route or "18m" in route, f"Should preserve tube/walk timing: {route}"

    @pytest.mark.asyncio
    async def test_triggers_on_long_walk_no_tfl_bus(self, monkeypatch):
        from houses.enricher import compute_lorena_commute
        from houses.models import TransitInfo

        no_bus = TransitInfo(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=None,
            route_summary="walk to Fleet (46m) → Train to Waterloo (42m)",
        )
        with_bus = TransitInfo(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=None,
            route_summary="walk to Fleet (46m) → Train to Waterloo (42m)",
        )
        google_bus = TransitInfo(
            destination_label="L (Google)",
            destination_postcode="EC3A 7LP",
            duration_minutes=55,
            daily_cost_gbp=3.8,
            route_summary="bus to Fleet → Train to Waterloo",
            bus_cost_gbp=3.8,
        )

        async def mock_transit(*_a, **_kw):
            return with_bus if _kw.get("allow_bus") else no_bus

        async def mock_google(*_):
            return google_bus

        monkeypatch.setattr("houses.enricher.compute_transit", mock_transit)
        monkeypatch.setattr("houses.enricher._compute_google_transit", mock_google)

        result = await compute_lorena_commute("GU52")
        assert result.bus_cost_gbp is not None, "Should find bus cost"
        assert result.bus_cost_gbp > 0
        assert result.duration_minutes is not None
        assert result.duration_minutes < 90, "Should be faster than TfL walk"

    @pytest.mark.asyncio
    async def test_skips_when_tfl_already_has_bus(self, monkeypatch):
        from houses.enricher import compute_lorena_commute
        from houses.models import TransitInfo

        no_bus = TransitInfo(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=None,
            route_summary="walk to Fleet (3m) → Train to Waterloo (42m)",
        )
        with_bus = TransitInfo(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=70,
            daily_cost_gbp=2.8,
            route_summary="bus to Fleet → Train to Waterloo",
        )

        async def mock_transit(*_a, **_kw):
            return with_bus if _kw.get("allow_bus") else no_bus

        monkeypatch.setattr("houses.enricher.compute_transit", mock_transit)
        monkeypatch.setattr("houses.enricher._compute_google_transit", lambda *_: None)

        result = await compute_lorena_commute("GU52")
        assert result is with_bus, "Should use TfL bus route when available"


class TestBusFaresDataLoaded:
    """data/bus_fares.json is loaded at runtime."""

    def test_file_loaded(self):
        from houses.enricher import _load_bus_fares

        data = _load_bus_fares()
        assert data is not None
        assert "_meta" in data
        assert data["_meta"]["national_max_single_gbp"] == 3.00

    def test_has_stagecoach_south(self):
        from houses.enricher import _load_bus_fares

        data = _load_bus_fares()
        assert "Stagecoach_South" in data
        assert "stop_zones" in data["Stagecoach_South"]
        assert "zone_fares" in data["Stagecoach_South"]


class TestParkingRates:
    """_load_parking_rates and _lookup_parking_cost with CSV."""

    def test_no_csv_file_returns_empty(self, monkeypatch):
        from houses.enricher import _load_parking_rates

        monkeypatch.setattr("houses.enricher._PARKING_RATES_PATH", Path("/tmp/nonexistent_parking_rates.csv"))
        by_name, by_crs = _load_parking_rates()
        assert by_name == {}
        assert by_crs == {}

    @pytest.mark.asyncio
    async def test_lookup_known_station(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nWoking,WOK,12.80\n")
        monkeypatch.setattr("houses.enricher._PARKING_RATES_PATH", csv_path)
        monkeypatch.setattr("houses.enricher._parking_rates_cache", None)

        async def _noop(_):
            return None

        monkeypatch.setattr("houses.enricher._apcoa_prebook_lookup", _noop)
        from houses.enricher import _lookup_parking_cost

        cost = await _lookup_parking_cost("Woking Rail Station")
        assert cost == 12.80

    @pytest.mark.asyncio
    async def test_lookup_free_station(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nMarlow,MLW,0.0\n")
        monkeypatch.setattr("houses.enricher._PARKING_RATES_PATH", csv_path)
        monkeypatch.setattr("houses.enricher._parking_rates_cache", None)

        async def _noop(_):
            return None

        monkeypatch.setattr("houses.enricher._apcoa_prebook_lookup", _noop)
        from houses.enricher import _lookup_parking_cost

        cost = await _lookup_parking_cost("Marlow Rail Station")
        assert cost == 0.0

    @pytest.mark.asyncio
    async def test_lookup_unknown_station(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nMarlow,MLW,0.0\n")
        monkeypatch.setattr("houses.enricher._PARKING_RATES_PATH", csv_path)
        monkeypatch.setattr("houses.enricher._parking_rates_cache", None)

        async def _noop(_):
            return None

        monkeypatch.setattr("houses.enricher._apcoa_prebook_lookup", _noop)
        from houses.enricher import _lookup_parking_cost

        cost = await _lookup_parking_cost("Unknown Station")
        assert cost is None

    @pytest.mark.asyncio
    async def test_lookup_blank_cost_returns_none(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nMarlow,MLW,\n")
        monkeypatch.setattr("houses.enricher._PARKING_RATES_PATH", csv_path)
        monkeypatch.setattr("houses.enricher._parking_rates_cache", None)

        async def _noop(_):
            return None

        monkeypatch.setattr("houses.enricher._apcoa_prebook_lookup", _noop)
        from houses.enricher import _lookup_parking_cost

        cost = await _lookup_parking_cost("Marlow Rail Station")
        assert cost is None

    @pytest.mark.asyncio
    async def test_lookup_by_crs_fallback(self, tmp_path, monkeypatch):
        """When name doesn't match, falls back to CRS lookup."""
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nCobham & Stoke Dabernon,CSD,8.10\n")
        monkeypatch.setattr("houses.enricher._PARKING_RATES_PATH", csv_path)
        monkeypatch.setattr("houses.enricher._parking_rates_cache", None)

        async def _noop(_):
            return None

        monkeypatch.setattr("houses.enricher._apcoa_prebook_lookup", _noop)
        from houses.enricher import _lookup_parking_cost

        # TfL returns "Cobham & Stoke D'Abernon Rail Station" — different from CSV name
        cost = await _lookup_parking_cost("Cobham & Stoke D'Abernon Rail Station")
        assert cost == 8.10


class TestExtractDailyRateFromTariff:
    """extract_daily_rate_from_tariff — pure function, no I/O, test fixture files."""

    def _load(self, name: str) -> str:
        return (FIXTURES_DIR / name).read_text()

    def test_woking(self):
        from scripts.sync_parking_rates import extract_daily_rate_from_tariff

        rate = extract_daily_rate_from_tariff(self._load("woking.txt"))
        assert rate == 12.80

    def test_fleet(self):
        from scripts.sync_parking_rates import extract_daily_rate_from_tariff

        rate = extract_daily_rate_from_tariff(self._load("fleet.txt"))
        assert rate == 10.90

    def test_bourne_end_peak_rate(self):
        """Bourne End has 'Daily Rate before 12pm: £4.00' — should pick peak rate."""
        from scripts.sync_parking_rates import extract_daily_rate_from_tariff

        rate = extract_daily_rate_from_tariff(self._load("bourne_end.txt"))
        assert rate == 4.00

    def test_didcot_24h_rate(self):
        """Didcot uses 'Up to 24 hours £7.20' format."""
        from scripts.sync_parking_rates import extract_daily_rate_from_tariff

        rate = extract_daily_rate_from_tariff(self._load("didcot_foxhall.txt"))
        assert rate == 7.20

    def test_high_wycombe(self):
        """High Wycombe has 'Daily Rate: £10.40' with a preceding 'Monday - Sunday' line."""
        from scripts.sync_parking_rates import extract_daily_rate_from_tariff

        rate = extract_daily_rate_from_tariff(self._load("high_wycombe.txt"))
        assert rate == 10.40

    def test_twyford_car_park_2(self):
        """Twyford CP2 has 'Daily Rate: £9.90'."""
        from scripts.sync_parking_rates import extract_daily_rate_from_tariff

        rate = extract_daily_rate_from_tariff(self._load("twyford_car_park_2.txt"))
        assert rate == 9.90

    def test_twyford_car_park_1_permit_only(self):
        """Twyford CP1 is permit holders only — returns None."""
        from scripts.sync_parking_rates import extract_daily_rate_from_tariff

        rate = extract_daily_rate_from_tariff(self._load("twyford_car_park_1.txt"))
        assert rate is None

    def test_empty_text_returns_none(self):
        from scripts.sync_parking_rates import extract_daily_rate_from_tariff

        assert extract_daily_rate_from_tariff("") is None

    def test_no_tariff_section_returns_none(self):
        from scripts.sync_parking_rates import extract_daily_rate_from_tariff

        assert extract_daily_rate_from_tariff("Some random text without pricing") is None


class TestNeTExParsing:
    """parse_netex_fares — extracts stop zones and fares from BODS NeTEx XML."""

    _STATIONS_CACHE: list[dict] | None = None

    @classmethod
    def _stations(cls) -> list[dict]:
        if cls._STATIONS_CACHE is None:
            import csv

            cls._STATIONS_CACHE = []
            with Path("data/stations.csv").open(newline="") as f:
                for row in csv.DictReader(f):
                    cls._STATIONS_CACHE.append(
                        {
                            "name": row["stationName"],
                            "crs": row["crsCode"],
                            "lat": float(row["lat"]),
                            "long": float(row["long"]),
                        }
                    )
        return cls._STATIONS_CACHE

    def test_parses_scso_stops_and_zones(self):
        """Stagecoach South dataset should find stops and zones."""
        xml = (Path("tests/fixtures/bods") / "scso_sample.xml").read_text()
        from scripts.extract_bus_fares import parse_netex_fares

        result = parse_netex_fares(xml, self._stations())
        assert result is not None
        assert len(result.get("stop_zones", {})) >= 1

    def test_parses_scso_zone_prices(self):
        """Stagecoach South dataset should extract adult_single prices for zone pairs.

        The real BODS fare data uses StartTariffZoneRef/EndTariffZoneRef
        and nests prices inside Tariff → FareStructureElement → PriceGroup
        instead of the simple AC Williams format.
        """
        xml = (Path("tests/fixtures/bods") / "scso_sample.xml").read_text()
        from scripts.extract_bus_fares import parse_netex_fares

        result = parse_netex_fares(xml, self._stations())
        assert result is not None
        fares = result.get("zone_fares", {})
        assert len(fares) >= 1
        any_single = any("adult_single" in v for v in fares.values())
        assert any_single, "No zone fare has an adult_single price"


class TestEnrichRailFares:
    """_enrich_rail_fares — adds NR fares when the cost is only bus/parking."""

    @pytest.mark.asyncio
    async def test_lorena_bus_cost_adds_rail_fare(self, monkeypatch, tmp_path):
        """Lorena with bus cost only (£4.00) gets rail fare (£37.20) added → £41.20."""
        # Point at fixture data files so nearest_station and fare_between work
        stations_csv = tmp_path / "stations.csv"
        stations_csv.write_text("stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\n")
        monkeypatch.setattr("houses.rail_fares._STATIONS_CSV", stations_csv)

        rail_csv = tmp_path / "rail_fares.csv"
        rail_csv.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,WAT,17.00\n")
        monkeypatch.setattr("houses.rail_fares._FARES_CSV", rail_csv)

        # Mock only the HTTP boundary: geocode returns synthetic coords.
        # nearest_station and fare_between run for real against tmp CSVs.
        async def mock_geocode(_):
            return (51.317, -0.556)

        monkeypatch.setattr("houses.server._geocode", mock_geocode)
        # nearest_station and fare_between read from the temp CSVs above — they run for real

        from houses.models import TransitInfo
        from houses.server import _enrich_rail_fares

        lorena = TransitInfo(
            destination_label="Lorena",
            destination_postcode="EC3A 7LP",
            duration_minutes=78,
            daily_cost_gbp=4.0,
            bus_cost_gbp=4.0,
        )
        simon = TransitInfo(
            destination_label="Simon",
            destination_postcode="SW1V 2QQ",
            duration_minutes=71,
            daily_cost_gbp=40.4,
        )
        await _enrich_rail_fares(
            enabled={"lorena"},
            postcode="GU21 7QF",
            address="St James Close",
            simon=simon,
            lorena=lorena,
        )
        # rail: (17.00 + 2.80) × 2 = 39.60. existing bus: 4.00. total: 43.60
        expected = 39.60 + 4.00
        assert lorena.daily_cost_gbp == pytest.approx(expected, rel=1e-2)

    @pytest.mark.asyncio
    async def test_simon_parking_cost_adds_rail_fare(self, monkeypatch, tmp_path):
        """Simon with parking cost only (£10.80) gets rail fare added → £50.40."""
        stations_csv = tmp_path / "stations.csv"
        stations_csv.write_text("stationName,crsCode,lat,long\nBrookwood,BKO,51.303,-0.636\n")
        monkeypatch.setattr("houses.rail_fares._STATIONS_CSV", stations_csv)

        rail_csv = tmp_path / "rail_fares.csv"
        rail_csv.write_text("origin_crs,dest_crs,single_fare_gbp\nBKO,VIC,17.00\n")
        monkeypatch.setattr("houses.rail_fares._FARES_CSV", rail_csv)

        async def mock_geocode(_):
            return (51.303, -0.636)

        monkeypatch.setattr("houses.server._geocode", mock_geocode)

        from houses.models import TransitInfo
        from houses.server import _enrich_rail_fares

        simon = TransitInfo(
            destination_label="Simon",
            destination_postcode="SW1V 2QQ",
            duration_minutes=71,
            daily_cost_gbp=10.8,
            parking_cost_gbp=10.8,
        )
        lorena = TransitInfo(
            destination_label="Lorena",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=None,
        )
        await _enrich_rail_fares(
            enabled={"simon"},
            postcode="GU21 2NA",
            address="Robin Hood Road, Knaphill",
            simon=simon,
            lorena=lorena,
        )
        # rail: (17.00 + 2.80) × 2 = 39.60. existing parking: 10.80. total: 50.40
        expected = 39.60 + 10.80
        assert simon.daily_cost_gbp == pytest.approx(expected, rel=1e-2)

    @pytest.mark.asyncio
    async def test_full_tfl_fare_skips_nr(self, monkeypatch):
        """When TfL already priced the journey, cost stays unchanged."""
        from houses.models import TransitInfo
        from houses.server import _enrich_rail_fares

        lorena = TransitInfo(
            destination_label="Lorena",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=36.0,
        )
        simon = TransitInfo(
            destination_label="Simon",
            destination_postcode="SW1V 2QQ",
            duration_minutes=71,
            daily_cost_gbp=40.4,
        )
        await _enrich_rail_fares(
            enabled={"simon", "lorena"},
            postcode="GU22 8RU",
            address="Test",
            simon=simon,
            lorena=lorena,
        )
        assert simon.daily_cost_gbp == 40.4
        assert lorena.daily_cost_gbp == 36.0


class TestKnownWrongBehaviours:
    """Tests for known bugs — these define expected correct behaviour."""

    def test_daily_cost_returns_return_when_no_single(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_return": 4.0})
        assert cost == 4.0, "Should fall back to return price when single is missing"

    def test_daily_cost_returns_day_when_no_single_no_return(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_day": 5.0})
        assert cost == 5.0, "Should use day price when single and return are missing"

    def test_daily_cost_uses_return_when_missing_single(self):
        from houses.enricher import _compute_bus_daily_cost

        cost = _compute_bus_daily_cost({"adult_return": 8.0}, {"national_max_single_gbp": 3.0})
        assert cost == 8.0, "Return is used as-is (national cap only applies to single)"

    def test_stop_coord_fallback_is_not_dead_code(self):
        """stop_coords should be populated from NaPTAN data during extraction."""
        from houses.enricher import _load_bus_fares

        data = _load_bus_fares()
        scso = data.get("Stagecoach_South", {})
        coords = scso.get("stop_coords", [])
        assert len(coords) > 0, "stop_coords empty — NaPTAN stop data not integrated or extraction needs re-run"
