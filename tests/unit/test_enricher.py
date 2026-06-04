"""Tests for enrichment logic."""

import pytest

from houses.enricher import (
    _END_PC_RE,
    _OUTCODE_RE,
    FEE_PAYING_TYPES,
    _boys_eligible,
    _compute_petrol_from_distance_km,
    _haversine_km,
    _phase_filter,
    _school_coords,
    _school_to_info,
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
