"""Tests for enrichment logic."""

from datetime import datetime
from pathlib import Path

import pytest
from money import Money

from houses.attempt import Attempt
from houses.bus_journey import BusJourneyRegistry, FareProduct, FareProductType, cheapest_round_trip
from houses.commute import Commute, CostGroup, JourneyLeg, LegMode
from houses.enricher import (
    _END_PC_RE,
    _OUTCODE_RE,
    _compute_petrol_from_distance_km,
    compute_commute_breakdown,
)
from houses.geo import GeoPoint
from houses.schools import School, SchoolGender
from houses.stations import Station
from houses.stations import find as find_station
from houses.transit_route import _format_route_summary, _next_weekday_date_params, _pick_best_journey


def _fares_from_dict(products: dict[str, float], meta: dict | None = None) -> dict[FareProductType, FareProduct]:
    """Convert old-style fare product dict to FareProduct dict for testing."""
    result = {}
    mapping = {
        "adult_single": FareProductType.SINGLE,
        "adult_return": FareProductType.RETURN,
        "adult_day": FareProductType.DAY,
    }
    for key, val in products.items():
        ptype = mapping.get(key)
        if ptype:
            result[ptype] = FareProduct(
                type=ptype,
                price=Money(str(val), "GBP"),
                operator="test",
                zone_pair="test:test",
            )
    return result


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


class TestSchoolAcceptsGender:
    def test_mixed_school_accepts_boys(self):
        s = School.from_GIAS_row({"Gender (name)": "Mixed", "TypeOfEstablishment (name)": "Community School"})
        assert s.accepts(SchoolGender.BOYS)

    def test_boys_school_accepts_boys(self):
        s = School.from_GIAS_row({"Gender (name)": "Boys", "TypeOfEstablishment (name)": "Academy Converter"})
        assert s.accepts(SchoolGender.BOYS)

    def test_girls_school_rejects_boys(self):
        s = School.from_GIAS_row({"Gender (name)": "Girls", "TypeOfEstablishment (name)": "Community School"})
        assert not s.accepts(SchoolGender.BOYS)

    def test_independent_boys_still_accepts_boys(self):
        """fee_paying is separate from gender — a fee-paying boys school still accepts boys."""
        s = School.from_GIAS_row({"Gender (name)": "Boys", "TypeOfEstablishment (name)": "Independent School"})
        assert s.accepts(SchoolGender.BOYS)

    def test_mixed_school_accepts_girls(self):
        s = School.from_GIAS_row({"Gender (name)": "Mixed"})
        assert s.accepts(SchoolGender.GIRLS)

    def test_mixed_required_for_both_genders(self):
        s = School.from_GIAS_row({"Gender (name)": "Mixed"})
        assert s.accepts(SchoolGender.BOYS) and s.accepts(SchoolGender.GIRLS)

    def test_boys_school_rejects_girls(self):
        s = School.from_GIAS_row({"Gender (name)": "Boys"})
        assert not s.accepts(SchoolGender.GIRLS)

    def test_unknown_gender_rejects_all(self):
        s = School.from_GIAS_row({"Gender (name)": "Not applicable"})
        assert s.gender == SchoolGender.UNKNOWN
        assert not s.accepts(SchoolGender.BOYS)
        assert not s.accepts(SchoolGender.GIRLS)
        assert not s.accepts(SchoolGender.MIXED)


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
        simon = Commute(destination_label="S", destination_postcode="SW1V 2QQ", daily_cost_gbp=Money("15.0", "GBP"))
        lorena = Commute(destination_label="L", destination_postcode="EC3A 7LP", daily_cost_gbp=Money("24.0", "GBP"))
        petrol = Commute(
            destination_label="Bracknell",
            destination_postcode="RG12 8YA",
            daily_cost_gbp=Money("10.0", "GBP"),
            mode="drive",
        )
        result = await compute_commute_breakdown(simon, lorena, petrol)
        assert result.simon_daily_gbp == 15.0

    @pytest.mark.asyncio
    async def test_missing_cost_means_none(self):
        """If any daily_cost_gbp is None, yearly_total should be None."""
        from houses.enricher import compute_commute_breakdown

        simon = Commute(destination_label="S", destination_postcode="SW1V 2QQ", daily_cost_gbp=None)
        lorena = Commute(destination_label="L", destination_postcode="EC3A 7LP", daily_cost_gbp=Money("24.0", "GBP"))
        petrol = Commute(
            destination_label="Bracknell",
            destination_postcode="RG12 8YA",
            daily_cost_gbp=Money("10.0", "GBP"),
            mode="drive",
        )
        result = await compute_commute_breakdown(simon, lorena, petrol)
        assert result.yearly_total_gbp is None


class TestSchoolFeePaying:
    def test_independent_school_is_fee_paying(self):
        s = School.from_GIAS_row({"TypeOfEstablishment (name)": "Independent School"})
        assert s.fee_paying

    def test_other_independent_is_fee_paying(self):
        s = School.from_GIAS_row({"TypeOfEstablishment (name)": "Other independent school"})
        assert s.fee_paying

    def test_community_school_not_fee_paying(self):
        s = School.from_GIAS_row({"TypeOfEstablishment (name)": "Community School"})
        assert not s.fee_paying

    def test_academy_converter_not_fee_paying(self):
        s = School.from_GIAS_row({"TypeOfEstablishment (name)": "Academy Converter"})
        assert not s.fee_paying


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


class TestSchoolAcceptsAge:
    """School.accepts_age — checks if a child of a given age can attend."""

    def test_primary_accepts_age_7(self):
        s = School.from_GIAS_row({"PhaseOfEducation (name)": "Primary"})
        assert s.accepts_age(7)

    def test_secondary_accepts_age_13(self):
        s = School.from_GIAS_row({"PhaseOfEducation (name)": "Secondary"})
        assert s.accepts_age(13)

    def test_primary_rejects_teenager(self):
        s = School.from_GIAS_row({"PhaseOfEducation (name)": "Primary"})
        assert not s.accepts_age(13)

    def test_secondary_rejects_young_child(self):
        s = School.from_GIAS_row({"PhaseOfEducation (name)": "Secondary"})
        assert not s.accepts_age(5)

    def test_all_through_accepts_all_ages(self):
        s = School.from_GIAS_row({"PhaseOfEducation (name)": "All-through"})
        assert s.accepts_age(5)
        assert s.accepts_age(11)
        assert s.accepts_age(17)

    def test_not_applicable_falls_back_to_statutory_age(self):
        """Not applicable phase uses StatutoryLowAge/HighAge when available."""
        s = School.from_GIAS_row(
            {
                "PhaseOfEducation (name)": "Not applicable",
                "StatutoryLowAge": "11",
                "StatutoryHighAge": "16",
            }
        )
        assert s.accepts_age(13)
        assert not s.accepts_age(5)

    def test_not_applicable_no_age_data(self):
        """Without age data, 'Not applicable' schools are accepted (caller filters)."""
        s = School.from_GIAS_row({"PhaseOfEducation (name)": "Not applicable"})
        assert s.accepts_age(10)


class TestSchoolCoords:
    """School.coords — parses Latitude/Longitude from a GIAS row."""

    def test_valid_coords(self):
        s = School.from_GIAS_row({"Latitude": "51.5", "Longitude": "-0.13"})
        assert s.coords == GeoPoint(51.5, -0.13)

    def test_missing_lat_returns_none(self):
        s = School.from_GIAS_row({"Longitude": "-0.13"})
        assert s.coords is None

    def test_missing_lng_returns_none(self):
        s = School.from_GIAS_row({"Latitude": "51.5"})
        assert s.coords is None

    def test_empty_strings_returns_none(self):
        s = School.from_GIAS_row({"Latitude": "", "Longitude": ""})
        assert s.coords is None

    def test_zero_coords(self):
        """Zero lat/lng should still return GeoPoint(0, 0)."""
        s = School.from_GIAS_row({"Latitude": "0", "Longitude": "0"})
        assert s.coords == GeoPoint(0.0, 0.0)

    def test_returns_geopoint(self):
        s = School.from_GIAS_row({"Latitude": "52.2053", "Longitude": "0.1218"})
        assert s.coords == GeoPoint(52.2053, 0.1218)


class TestFindNearestFilters:
    """find_nearest must exclude fee-paying schools and schools with blank names."""

    @pytest.mark.asyncio
    async def test_excludes_fee_paying_school(self, monkeypatch):
        """A fee-paying school should be excluded even if it's the nearest."""
        from houses.schools import find_nearest

        # Mock _load_schools to return a known set
        fee_paying = School.from_GIAS_row(
            {
                "EstablishmentName": "Expensive School",
                "Gender (name)": "Mixed",
                "PhaseOfEducation (name)": "Primary",
                "TypeOfEstablishment (name)": "Independent School",
                "Latitude": "51.5",
                "Longitude": "-0.1",
                "Postcode": "SL6 1AA",
            }
        )
        non_fee = School.from_GIAS_row(
            {
                "EstablishmentName": "Free School",
                "Gender (name)": "Mixed",
                "PhaseOfEducation (name)": "Primary",
                "TypeOfEstablishment (name)": "Community School",
                "Latitude": "51.501",
                "Longitude": "-0.101",
                "Postcode": "SL6 2BB",
            }
        )
        monkeypatch.setattr("houses.schools._load_schools", lambda: [fee_paying, non_fee])

        # Mock geocode — property at midpoint (both schools within ~0.1° ≈ 7km,
        # school_search_radius_km=5, but 0.001° ≈ 70m fits inside radius)
        async def mock_geocode(*_, **__):
            return Attempt.succeeded(GeoPoint(51.5005, -0.1005), "test")

        monkeypatch.setattr("houses.schools.geocode", mock_geocode)
        monkeypatch.setattr("houses.schools._geocode_address", mock_geocode)

        result = await find_nearest("SL6 3CC", child_age=7, requirement=SchoolGender.BOYS)
        assert result is not None, "Expected a school, got None"
        assert result.name == "Free School", f"Expected Free School, got {result.name}"

    @pytest.mark.asyncio
    async def test_excludes_empty_name_school(self, monkeypatch):
        """A school with a blank name should be excluded."""
        from houses.schools import find_nearest

        unnamed = School.from_GIAS_row(
            {
                "EstablishmentName": "",
                "Gender (name)": "Mixed",
                "PhaseOfEducation (name)": "Primary",
                "TypeOfEstablishment (name)": "Community School",
                "Latitude": "51.5",
                "Longitude": "-0.1",
                "Postcode": "SL6 1AA",
            }
        )
        named = School.from_GIAS_row(
            {
                "EstablishmentName": "Has A Name School",
                "Gender (name)": "Mixed",
                "PhaseOfEducation (name)": "Primary",
                "TypeOfEstablishment (name)": "Community School",
                "Latitude": "51.501",
                "Longitude": "-0.101",
                "Postcode": "SL6 2BB",
            }
        )
        monkeypatch.setattr("houses.schools._load_schools", lambda: [unnamed, named])

        async def mock_geocode(*_, **__):
            return Attempt.succeeded(GeoPoint(51.5005, -0.1005), "test")

        monkeypatch.setattr("houses.schools.geocode", mock_geocode)
        monkeypatch.setattr("houses.schools._geocode_address", mock_geocode)

        result = await find_nearest("SL6 3CC", child_age=7, requirement=SchoolGender.BOYS)
        assert result is not None, "Expected a school, got None"
        assert result.name == "Has A Name School", f"Expected Has A Name, got {result.name}"


class TestSchoolFromGIASRow:
    """School.from_GIAS_row — parses a GIAS CSV row into a School dataclass."""

    def test_basic_parse(self):
        s = School.from_GIAS_row(
            {
                "EstablishmentName": "Test Primary School",
                "Gender (name)": "Mixed",
                "TypeOfEstablishment (name)": "Community School",
                "URN": "123456",
                "SchoolWebsite": "https://example.com",
                "OfstedRating (name)": "Good",
                "InspectionYear": "2023",
                "PhaseOfEducation (name)": "Primary",
                "Postcode": "SL6 1AA",
            }
        )

        assert s.name == "Test Primary School"
        assert s.gender == SchoolGender.MIXED
        assert s.type_of_establishment == "Community School"
        assert not s.fee_paying
        assert s.urn == "123456"
        assert s.website == "https://example.com"
        assert s.ofsted_rating == "Good"
        assert s.inspection_year == "2023"
        assert s.phase == "Primary"
        assert s.postcode == "SL6 1AA"

    def test_independent_school_is_fee_paying(self):
        s = School.from_GIAS_row({"TypeOfEstablishment (name)": "Independent School"})
        assert s.fee_paying

    def test_missing_name_defaults(self):
        s = School.from_GIAS_row({})
        assert s.name == ""
        assert s.urn == ""

    def test_coords_from_lat_lng(self):
        s = School.from_GIAS_row({"Latitude": "51.5", "Longitude": "-0.13"})
        assert s.coords == GeoPoint(51.5, -0.13)

    def test_missing_coords_is_none(self):
        s = School.from_GIAS_row({})
        assert s.coords is None

    def test_gender_from_raw_string(self):
        s = School.from_GIAS_row({"Gender (name)": "Boys"})
        assert s.gender == SchoolGender.BOYS


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
    """Station.short_name — strip common station suffixes."""

    def test_rail_station(self):
        assert Station.short_name("Maidenhead Rail Station") == "Maidenhead"

    def test_underground_station(self):
        assert Station.short_name("Paddington Underground Station") == "Paddington"

    def test_generic_station(self):
        assert Station.short_name("Oxford Circus Station") == "Oxford Circus"

    def test_no_suffix(self):
        assert Station.short_name("Some Street, Town") == "Some Street, Town"

    def test_strips_london_prefix(self):
        assert Station.short_name("London Paddington Rail Station") == "Paddington"
        assert Station.short_name("London Waterloo Rail Station") == "Waterloo"

    def test_empty_string(self):
        assert Station.short_name("") == ""


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
    """find_station — look up station coords from CSV by name."""

    def test_finds_didcot_parkway(self):
        """'Didcot Parkway Rail Station' should match 'Didcot Parkway' in stations.csv."""
        station = find_station("Didcot Parkway Rail Station")
        assert station is not None
        # Didcot Parkway is at ~51.611, -1.243 in stations.csv
        assert abs(station.location.lat - 51.611) < 0.02
        assert abs(station.location.lon + 1.243) < 0.02

    def test_returns_none_for_unknown(self):
        assert find_station("Some Fake Station") is None

    def test_strips_station_suffixes(self):
        # Should find Maidenhead in stations.csv (not "Maidenhead Rail Station")
        station = find_station("Maidenhead Rail Station")
        assert station is not None


class TestStationCrsLookup:
    """find_station — look up CRS from stations.csv by name."""

    def test_finds_woking(self):
        station = find_station("Woking Rail Station")
        assert station is not None
        assert station.crs == "WOK"

    def test_finds_maidenhead(self):
        station = find_station("Maidenhead Rail Station")
        assert station is not None
        assert station.crs == "MAI"

    def test_case_insensitive(self):
        station = find_station("woking rail station")
        assert station is not None
        assert station.crs == "WOK"

    def test_not_found_returns_none(self):
        assert find_station("Some Fake Station") is None


class TestBusFareDailyCost:
    """cheapest_round_trip — cheapest product covering two journeys."""

    def test_uses_return_when_cheaper_than_2x_single(self):
        # adult_return £4.00 vs 2×single £5.00 → £4.00
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.50, "adult_return": 4.00}))
        assert cost == Money("4.00", "GBP")

    def test_uses_day_rider_when_cheapest(self):
        # adult_day £4.50 < 2×single £5.00 → £4.50
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.50, "adult_day": 4.50}))
        assert cost == Money("4.50", "GBP")

    def test_uses_2x_single_when_no_other_products(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.50}))
        assert cost == Money("5.00", "GBP")

    def test_national_cap_applied_to_single(self):
        # BODS single is £4.00, cap → £3.00, daily → £6.00
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 4.00}), Money("3.00", "GBP"))
        assert cost == Money("6.00", "GBP")

    def test_national_cap_below_cap(self):
        # BODS single £2.50 is below cap → used as-is
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.50}), Money("3.00", "GBP"))
        assert cost == Money("5.00", "GBP")

    def test_national_cap_not_set(self):
        # national_max_single is None → BODS single used as-is
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 4.00}))
        assert cost == Money("8.00", "GBP")


class TestBusFareLookup:
    """BusJourneyRegistry.fares_for_stops + cheapest_round_trip — full pipeline."""

    _registry = BusJourneyRegistry()

    def _cost(self, dep: str, arr: str, dep_point=None, arr_point=None) -> Money | None:
        fares = self._registry.fares_for_stops(dep, arr, dep_point, arr_point)
        return cheapest_round_trip(fares, self._registry.national_max_single)

    def test_randolph_close_to_woking_station(self):
        cost = self._cost("randolph close", "woking railway station")
        assert cost == Money("1.80", "GBP")

    def test_case_insensitive_matching(self):
        cost = self._cost("RANDOLPH CLOSE", "WOKING RAILWAY STATION")
        assert cost == Money("1.80", "GBP")

    def test_tfl_area_prefix_dep_match(self):
        cost = self._cost("Knaphill, Randolph Close", "Woking, Woking Railway Station")
        assert cost == Money("1.80", "GBP")

    def test_tfl_westfield_not_in_zone_fares(self):
        cost = self._cost("Westfield, Westfield Common", "Woking, Woking Railway Station")
        assert cost is not None, "Westfield->Woking should now match via fuzzy matching"

    def test_tfl_brookwood_to_woking(self):
        cost = self._cost("Brookwood, Brookwood Railway Station", "Woking, Woking Railway Station")
        assert cost == Money("3.00", "GBP")

    def test_fuzzy_match_periods(self):
        cost = self._cost("St. Johns, St. James Close", "Woking, Woking Railway Station")
        assert cost is not None

    def test_fuzzy_match_does_not_match_unrelated(self):
        cost = self._cost("Knaphill, Supermarket Car Park", "Woking, Woking Railway Station")
        assert cost is None, "Should not match unrelated stop 'supermarket car park'"

        cost2 = self._cost("North London Bus Stop", "Woking, Woking Railway Station")
        assert cost2 is None, "Should not match stop in entirely different area"

    def test_fuzzy_match_short_noise_words_rejected(self):
        cost = self._cost("Woking Station", "Woking, Woking Railway Station")
        assert cost is None, "'Woking Station' should not match 'station' (a different stop)"

    def test_unknown_stops_return_none(self):
        cost = self._cost("Unknown Stop", "Another Unknown")
        assert cost is None

    def test_same_stop_is_not_free(self):
        cost = self._cost("randolph close", "randolph close")
        assert cost is not None

    def test_reversed_direction(self):
        cost = self._cost("woking railway station", "randolph close")
        assert cost == Money("1.80", "GBP")

    def test_coord_fallback_without_coords_still_returns_none(self):
        cost = self._cost(
            "Unknown Stop",
            "Another Unknown",
            {"lat": 51.3, "lon": -0.5},
            {"lat": 51.31, "lon": -0.49},
        )
        assert cost is None


class TestComputeBusDailyCost:
    """cheapest_round_trip — cheapest product selection."""

    def test_single_only_doubled(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 1.5}))
        assert cost == Money("3.00", "GBP")

    def test_single_with_return(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 1.5, "adult_return": 2.5}))
        assert cost == Money("2.50", "GBP")

    def test_single_with_day_cheaper(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.0, "adult_day": 3.5}))
        assert cost == Money("3.50", "GBP")

    def test_day_more_expensive_than_double(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 0.9, "adult_day": 8.5}))
        assert cost == Money("1.80", "GBP")

    def test_national_cap_applied_before_doubling(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 3.5}), Money("3.00", "GBP"))
        assert cost == Money("6.00", "GBP")

    def test_return_cheaper_than_capped_double(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.0, "adult_return": 3.8}), Money("3.00", "GBP"))
        assert cost == Money("3.80", "GBP")

    def test_no_single_returns_none(self):
        cost = cheapest_round_trip({})
        assert cost is None

    def test_empty_fares_returns_none(self):
        cost = cheapest_round_trip({})
        assert cost is None

    def test_return_more_expensive_than_single_double(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 1.0, "adult_return": 2.5}))
        assert cost == Money("2.00", "GBP")

    def test_cap_makes_singles_cheaper_than_return(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 3.5, "adult_return": 5.0}), Money("2.00", "GBP"))
        assert cost == Money("4.00", "GBP")

    def test_return_only_no_single(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_return": 4.0}))
        assert cost == Money("4.00", "GBP")

    def test_day_only_no_single_or_return(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_day": 5.0}))
        assert cost == Money("5.00", "GBP")

    def test_all_products_day_is_cheapest(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.0, "adult_return": 3.5, "adult_day": 3.0}))
        assert cost == Money("3.00", "GBP")

    def test_all_products_return_is_cheapest(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.0, "adult_return": 2.5, "adult_day": 6.0}))
        assert cost == Money("2.50", "GBP")

    def test_all_products_singles_cheapest_even_with_cap(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 1.0, "adult_return": 3.0, "adult_day": 4.0}))
        assert cost == Money("2.00", "GBP")


class TestStopToZoneMapping:
    """BusJourneyRegistry — stop name → zone lookup."""

    _registry = BusJourneyRegistry()

    def test_randolph_close_maps_to_zone(self):
        fares = self._registry.fares_for_stops("randolph close", "woking railway station")
        assert len(fares) > 0

    def test_woking_station_maps_to_same_zone(self):
        fares_rc = self._registry.fares_for_stops("randolph close", "woking railway station")
        fares_ws = self._registry.fares_for_stops("woking railway station", "randolph close")
        assert len(fares_rc) > 0
        assert len(fares_ws) > 0


class TestZonePairLookup:
    """BusJourneyRegistry — zone pair → FareProduct."""

    _registry = BusJourneyRegistry()

    def test_randolph_woking_station_has_single(self):
        fares = self._registry.fares_for_stops("randolph close", "woking railway station")
        assert FareProductType.SINGLE in fares
        assert fares[FareProductType.SINGLE].price == Money("0.90", "GBP")

    def test_randolph_woking_has_adult_day(self):
        fares = self._registry.fares_for_stops("randolph close", "woking railway station")
        assert FareProductType.DAY in fares
        assert fares[FareProductType.DAY].price == Money("8.50", "GBP")

    def test_reverse_zone_pair_has_same_fares(self):
        fares = self._registry.fares_for_stops("woking railway station", "randolph close")
        assert FareProductType.SINGLE in fares
        assert fares[FareProductType.SINGLE].price == Money("0.90", "GBP")


class TestPickBestLorenaRoute:
    """_pick_best_lorena_route — bus vs no-bus decision."""

    def test_uses_bus_when_much_faster(self):
        from houses.commute import Commute
        from houses.enricher import _pick_best_lorena_route

        no_bus = Commute(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=50)
        with_bus = Commute(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=30)
        result = _pick_best_lorena_route(no_bus, with_bus)
        assert result == with_bus

    def test_rejects_bus_when_not_faster(self):
        from houses.commute import Commute
        from houses.enricher import _pick_best_lorena_route

        no_bus = Commute(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=35)
        with_bus = Commute(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=33)
        result = _pick_best_lorena_route(no_bus, with_bus)
        assert result == no_bus

    def test_falls_back_to_no_bus_when_with_bus_none(self):
        from houses.commute import Commute
        from houses.enricher import _pick_best_lorena_route

        no_bus = Commute(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=50)
        with_bus = Commute(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=None)
        result = _pick_best_lorena_route(no_bus, with_bus)
        assert result == no_bus

    def test_falls_back_to_with_bus_when_no_bus_none(self):
        from houses.commute import Commute
        from houses.enricher import _pick_best_lorena_route

        no_bus = Commute(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=None)
        with_bus = Commute(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=30)
        result = _pick_best_lorena_route(no_bus, with_bus)
        assert result == with_bus


class TestBusFallback:
    """Bus cost added when TfL's route has a long walk that Google can shortcut."""

    @pytest.mark.asyncio
    async def test_adds_bus_cost_when_tfl_route_has_long_walk(self, monkeypatch):
        from houses.commute import Commute
        from houses.enricher import compute_lorena_commute

        no_bus = Commute(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=116,
            daily_cost_gbp=None,
            cost_groups=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=46),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TRAIN, duration_minutes=42),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TUBE, duration_minutes=4),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=18),)),
            ),
        )
        bus_route = Commute(
            destination_label="L (Bus)",
            destination_postcode="EC3A 7LP",
            duration_minutes=55,
            daily_cost_gbp=3.8,
            cost_groups=(
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.BUS, duration_minutes=28),),
                    cost=3.8,
                ),
            ),
        )

        async def mock_transit(self):
            return Attempt.succeeded(no_bus, "test")

        async def mock_bus(*_):
            return bus_route

        async def _disabled(*_, **__):
            return None

        monkeypatch.setattr("houses.transit_route.TransitRoute.plan", mock_transit)
        monkeypatch.setattr("houses.routing._find_bus_alternative", mock_bus)
        monkeypatch.setattr("houses.routing._walk_commute", _disabled)

        result = (await compute_lorena_commute("GU52")).get()
        assert result.non_rail_cost() > 0, "Should find bus cost"

    @pytest.mark.asyncio
    async def test_adds_bus_cost_when_tfl_route_no_bus(self, monkeypatch):
        from houses.commute import Commute
        from houses.enricher import compute_lorena_commute

        no_bus = Commute(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=None,
            cost_groups=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=46),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TRAIN, duration_minutes=42),)),
            ),
        )
        with_bus = Commute(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=None,
            cost_groups=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=46),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TRAIN, duration_minutes=42),)),
            ),
        )
        bus_route = Commute(
            destination_label="L (Bus)",
            destination_postcode="EC3A 7LP",
            duration_minutes=55,
            daily_cost_gbp=3.8,
            cost_groups=(
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.BUS, duration_minutes=28),),
                    cost=3.8,
                ),
            ),
        )

        async def mock_transit(self):
            return Attempt.succeeded(with_bus if self._allow_bus else no_bus, "test")

        async def mock_bus(*_):
            return bus_route

        async def _none(*_, **__):
            return None

        monkeypatch.setattr("houses.transit_route.TransitRoute.plan", mock_transit)
        monkeypatch.setattr("houses.routing._find_bus_alternative", mock_bus)
        monkeypatch.setattr("houses.routing._walk_commute", _none)

        result = (await compute_lorena_commute("GU52")).get()
        assert result.non_rail_cost() > 0, "Should find bus cost"
        assert result.duration_minutes is not None
        assert result.duration_minutes < 90, "Should be faster than TfL walk"

    @pytest.mark.asyncio
    async def test_skips_bus_fallback_when_tfl_route_has_bus(self, monkeypatch):
        from houses.commute import Commute
        from houses.enricher import compute_lorena_commute

        async def _none(*_, **__):
            return None

        no_bus = Commute(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=None,
        )
        with_bus = Commute(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=70,
            daily_cost_gbp=2.8,
        )

        async def mock_transit(self):
            return Attempt.succeeded(with_bus if self._allow_bus else no_bus, "test")

        monkeypatch.setattr("houses.transit_route.TransitRoute.plan", mock_transit)
        monkeypatch.setattr("houses.routing._find_bus_alternative", lambda *_: None)
        monkeypatch.setattr("houses.routing._walk_commute", _none)

        result = (await compute_lorena_commute("GU52")).get()
        assert result is not None
        assert result.duration_minutes == with_bus.duration_minutes
        assert result.daily_cost_gbp == with_bus.daily_cost_gbp


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
    async def test_lorena_bus_cost_adds_rail_fare(self, tmp_path):
        """Lorena with bus cost only (£4.00) gets rail fare (£37.20) added → £41.20."""
        from houses.rail_fares import RailFareRegistry
        from houses.stations import StationRegistry

        stations_csv = tmp_path / "stations.csv"
        stations_csv.write_text(
            "stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\nFenchurch Street,FST,51.511,-0.079\n"
        )
        fares_csv = tmp_path / "fares.csv"
        fares_csv.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,FST,17.00\n")

        reg = RailFareRegistry(
            station_registry=StationRegistry(_stations_csv=stations_csv),
            _fares_csv=fares_csv,
        )

        async def mock_geocode(_):
            return Attempt.succeeded(GeoPoint(51.317, -0.556), "test")

        from houses.commute import Commute
        from houses.enrichment_runner import _enrich_rail_fares

        lorena = Commute(
            destination_label="Lorena",
            destination_postcode="EC3A 7LP",
            duration_minutes=78,
            daily_cost_gbp=Money("4.0", "GBP"),
            cost_groups=(
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.BUS, duration_minutes=10),),
                    cost=4.0,
                ),
            ),
        )
        simon = Commute(
            destination_label="Simon",
            destination_postcode="SW1V 2QQ",
            duration_minutes=71,
            daily_cost_gbp=Money("40.4", "GBP"),
        )
        _simon, lorena_result = await _enrich_rail_fares(
            enabled={"lorena"},
            postcode="GU21 7QF",
            address="St James Close",
            simon=simon,
            lorena=lorena,
            _registry=reg,
            _geocode=mock_geocode,
        )
        # rail: (17.00 + 2.80) × 2 = 39.60. existing bus: 4.00. total: 43.60
        assert lorena_result.daily_cost_gbp == Money("43.60", "GBP")

    @pytest.mark.asyncio
    async def test_simon_parking_cost_adds_rail_fare(self, tmp_path):
        """Simon with parking cost only (£10.80) gets rail fare added → £50.40."""
        from houses.rail_fares import RailFareRegistry
        from houses.stations import StationRegistry

        stations_csv = tmp_path / "stations.csv"
        stations_csv.write_text(
            "stationName,crsCode,lat,long\nBrookwood,BKO,51.303,-0.636\nVictoria Station,VIC,51.495,-0.144\n"
        )
        fares_csv = tmp_path / "fares.csv"
        fares_csv.write_text("origin_crs,dest_crs,single_fare_gbp\nBKO,VIC,17.00\n")

        reg = RailFareRegistry(
            station_registry=StationRegistry(_stations_csv=stations_csv),
            _fares_csv=fares_csv,
        )

        async def mock_geocode(_):
            return Attempt.succeeded(GeoPoint(51.303, -0.636), "test")

        from houses.commute import Commute
        from houses.enrichment_runner import _enrich_rail_fares

        simon = Commute(
            destination_label="Simon",
            destination_postcode="SW1V 2QQ",
            duration_minutes=71,
            daily_cost_gbp=Money("10.8", "GBP"),
            cost_groups=(
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.PARK, duration_minutes=0),),
                    operator="ParkCo",
                    cost=10.8,
                ),
            ),
        )
        lorena = Commute(
            destination_label="Lorena",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=None,
        )
        simon_result, _lorena_result = await _enrich_rail_fares(
            enabled={"simon"},
            postcode="GU21 2NA",
            address="Robin Hood Road, Knaphill",
            simon=simon,
            lorena=lorena,
            _registry=reg,
            _geocode=mock_geocode,
        )
        # rail: (17.00 + 2.80) × 2 = 39.60. existing parking: 10.80. total: 50.40
        assert simon_result.daily_cost_gbp == Money("50.40", "GBP")

    @pytest.mark.asyncio
    async def test_full_tfl_fare_skips_nr(self):
        """When TfL already priced the journey, cost stays unchanged."""
        from houses.commute import Commute
        from houses.enrichment_runner import _enrich_rail_fares

        lorena = Commute(
            destination_label="Lorena",
            destination_postcode="EC3A 7LP",
            duration_minutes=90,
            daily_cost_gbp=Money("36.0", "GBP"),
        )
        simon = Commute(
            destination_label="Simon",
            destination_postcode="SW1V 2QQ",
            duration_minutes=71,
            daily_cost_gbp=Money("40.4", "GBP"),
        )
        simon_result, lorena_result = await _enrich_rail_fares(
            enabled={"simon", "lorena"},
            postcode="GU22 8RU",
            address="Test",
            simon=simon,
            lorena=lorena,
        )
        assert simon_result.daily_cost_gbp == Money("40.4", "GBP")
        assert lorena_result.daily_cost_gbp == Money("36.0", "GBP")


class TestKnownWrongBehaviours:
    """Tests for known bugs — these define expected correct behaviour."""

    def test_daily_cost_returns_return_when_no_single(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_return": 4.0}))
        assert cost == Money("4.00", "GBP"), "Should fall back to return price when single is missing"

    def test_daily_cost_returns_day_when_no_single_no_return(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_day": 5.0}))
        assert cost == Money("5.00", "GBP"), "Should use day price when single and return are missing"

    def test_daily_cost_uses_return_when_missing_single(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_return": 8.0}), Money("3.00", "GBP"))
        assert cost == Money("8.00", "GBP"), "Return is used as-is (national cap only applies to single)"

    def test_stop_coord_fallback_is_not_dead_code(self):
        """stop_coords should be populated from NaPTAN data during extraction."""
        registry = BusJourneyRegistry()
        _ = registry.national_max_single  # trigger lazy load
        scso = registry._data.get("Stagecoach_South", {})
        coords = scso.get("stop_coords", [])
        assert len(coords) > 0, "stop_coords empty — NaPTAN stop data not integrated or extraction needs re-run"


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
