from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dag.attempt import Attempt

if TYPE_CHECKING:
    from houses.school import School

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.geopoint import GeoPoint


async def _fake_geocode(*_, **__):
    from dag.attempt import Attempt
    from houses.geopoint import GeoPoint

    return Attempt.succeeded(GeoPoint(51.5005, -0.1005))


@pytest.fixture(autouse=True)
def _fake_svc():
    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services

    token = _sp.set(make_services())
    try:
        yield
    finally:
        _sp.reset(token)


@pytest.mark.asyncio
async def test_primary_school_impossible_without_location():
    from houses.nodes.schools import PrimarySchoolNode

    loc = UserInputNode[GeoPoint]("loc_ps", GeoPoint)
    addr = UserInputNode[str]("addr_ps", str)
    addr.push("10 High St, SW1V 2QQ", "test")
    await flush_processor()
    node = PrimarySchoolNode("ps", best_location=loc, best_address=addr)
    await flush_processor()
    a = await node.attempt()
    assert a.impossible or a.pending  # location dep is pending → impossible or pending


@pytest.mark.asyncio
async def test_secondary_school_impossible_without_location():
    from houses.nodes.schools import SecondarySchoolNode

    loc = UserInputNode[GeoPoint]("loc_ss", GeoPoint)
    addr = UserInputNode[str]("addr_ss", str)
    addr.push("10 High St, SW1V 2QQ", "test")
    await flush_processor()
    node = SecondarySchoolNode("ss", best_location=loc, best_address=addr)
    await flush_processor()
    a = await node.attempt()
    assert a.impossible or a.pending


@pytest.mark.asyncio
async def test_primary_school_impossible_without_address():
    from houses.nodes.schools import PrimarySchoolNode

    loc = UserInputNode[GeoPoint]("loc_ps2", GeoPoint)
    loc.push(GeoPoint(51.5, -0.1), "test")
    await flush_processor()
    addr = UserInputNode[str]("addr_ps2", str)
    node = PrimarySchoolNode("ps2", best_location=loc, best_address=addr)
    await flush_processor()
    a = await node.attempt()
    assert a.impossible or a.pending


@pytest.mark.asyncio
async def test_secondary_school_impossible_without_address():
    from houses.nodes.schools import SecondarySchoolNode

    loc = UserInputNode[GeoPoint]("loc_ss2", GeoPoint)
    loc.push(GeoPoint(51.5, -0.1), "test")
    await flush_processor()
    addr = UserInputNode[str]("addr_ss2", str)
    node = SecondarySchoolNode("ss2", best_location=loc, best_address=addr)
    await flush_processor()
    a = await node.attempt()
    assert a.impossible or a.pending


@pytest.mark.asyncio
async def test_school_location_node_fails_without_school():
    from dag.user_input_node import UserInputNode
    from houses.nodes.schools import SchoolLocationNode

    school = UserInputNode[dict]("sn", dict)
    node = SchoolLocationNode("sln", school_node=school)
    a = await node.attempt()
    assert not a.succeeded


@pytest.mark.asyncio
async def test_school_location_node_prefers_full_address_over_latlon():
    """Regression: the school walk destination was a bare 'lat,lon'.
    The destination must be the school NAME joined with the address
    captured when the school was first found — never coordinates."""
    from houses.nodes.schools import SchoolLocationNode

    school = UserInputNode[dict]("sn_addr", dict)
    node = SchoolLocationNode("sln_addr", school_node=school)
    school.push(
        {
            "name": "Larchfield Primary School",
            "postcode": "SL6 4ET",
            "full_address": "Bargeman Road, Maidenhead SL6 4ET",
            "lat": 51.52,
            "lon": -0.72,
        },
        "test",
    )
    await flush_processor()
    a = await node.attempt()
    assert a.succeeded
    assert a.value_or_none() == "Larchfield Primary School, Bargeman Road, Maidenhead SL6 4ET"


@pytest.mark.asyncio
async def test_school_location_node_falls_back_to_name_postcode():
    """No full_address → name + postcode (still readable, not lat/lon)."""
    from houses.nodes.schools import SchoolLocationNode

    school = UserInputNode[dict]("sn_np", dict)
    node = SchoolLocationNode("sln_np", school_node=school)
    school.push({"name": "Larchfield Primary School", "postcode": "SL6 4ET", "lat": 51.52, "lon": -0.72}, "test")
    await flush_processor()
    a = await node.attempt()
    assert a.succeeded
    assert a.value_or_none() == "Larchfield Primary School, SL6 4ET"


@pytest.mark.asyncio
async def test_secondary_school_returns_impossible_when_no_school_found():
    """When school lookup returns None, secondary school must return
    Attempt.impossible (not crash with AttributeError)."""
    from houses.nodes.schools import SecondarySchoolNode
    from houses.services_provider import _request_services as _sp
    from tests.helpers import FakeSchoolLookup, make_services

    token = _sp.set(make_services(school_lookup=FakeSchoolLookup(school=None)))
    try:
        loc = UserInputNode[GeoPoint]("loc_ss3", GeoPoint)
        addr = UserInputNode[str]("addr_ss3", str)
        node = SecondarySchoolNode("ss3", best_location=loc, best_address=addr)
        loc.push(GeoPoint(51.5, -0.1), "test")
        addr.push("10 High St, London, SW1V 2QQ", "test")
        await flush_processor()
        a = await node.attempt()
        assert not a.succeeded
        assert "no secondary school found" in a.error
    finally:
        _sp.reset(token)


@pytest.mark.asyncio
async def test_primary_school_returns_impossible_when_no_school_found():
    """Primary school must return Attempt.impossible when lookup returns None."""
    from houses.nodes.schools import PrimarySchoolNode
    from houses.services_provider import _request_services as _sp
    from tests.helpers import FakeSchoolLookup, make_services

    token = _sp.set(make_services(school_lookup=FakeSchoolLookup(school=None)))
    try:
        loc = UserInputNode[GeoPoint]("loc_ps3", GeoPoint)
        addr = UserInputNode[str]("addr_ps3", str)
        node = PrimarySchoolNode("ps3", best_location=loc, best_address=addr)
        loc.push(GeoPoint(51.5, -0.1), "test")
        addr.push("10 High St, London, SW1V 2QQ", "test")
        await flush_processor()
        a = await node.attempt()
        assert not a.succeeded
        assert "no primary school found" in a.error
    finally:
        _sp.reset(token)


class TestSchoolGenderFiltering:
    """School.accepts_any filters schools by acceptable genders."""

    def _make_school(self, gender: str) -> School:  # noqa: F821
        from houses.geopoint import GeoPoint
        from houses.school import School
        from houses.school_gender import SchoolGender

        return School(
            urn="test",
            name="Test School",
            phase="primary",
            gender=SchoolGender(gender),
            type_of_establishment="community school",
            postcode="SW1V 2QQ",
            website="",
            ofsted_rating="Good",
            inspection_year="2022",
            coords=GeoPoint(51.5, -0.13),
            statutory_low_age=None,
            statutory_high_age=None,
        )

    def test_accepts_any_boys_school_with_boys_only(self):
        school = self._make_school("boys")
        from houses.school_gender import SchoolGender

        assert school.accepts_any((SchoolGender.BOYS,))
        assert not school.accepts_any((SchoolGender.GIRLS,))

    def test_accepts_any_girls_school_with_girls_only(self):
        school = self._make_school("girls")
        from houses.school_gender import SchoolGender

        assert school.accepts_any((SchoolGender.GIRLS,))
        assert not school.accepts_any((SchoolGender.BOYS,))

    def test_accepts_any_mixed_school_with_mixed_only(self):
        school = self._make_school("mixed")
        from houses.school_gender import SchoolGender

        assert school.accepts_any((SchoolGender.MIXED,))
        assert not school.accepts_any((SchoolGender.BOYS,))
        assert not school.accepts_any((SchoolGender.GIRLS,))

    def test_accepts_any_all_types_accepts_any_school(self):
        school = self._make_school("boys")
        from houses.school_gender import SchoolGender

        all_types = (SchoolGender.BOYS, SchoolGender.GIRLS, SchoolGender.MIXED)
        assert school.accepts_any(all_types)

    def test_accepts_any_rejects_unknown(self):
        school = self._make_school("unknown")
        from houses.school_gender import SchoolGender

        assert not school.accepts_any((SchoolGender.BOYS, SchoolGender.GIRLS, SchoolGender.MIXED))

    def test_accepts_any_empty_accepts_nothing(self):
        school = self._make_school("boys")
        assert not school.accepts_any(())


class TestSchoolNodeAcceptable:
    """School nodes pass the acceptable tuple to find_nearest."""

    @pytest.mark.asyncio
    async def test_primary_school_uses_custom_acceptable(self):
        from houses.nodes.schools import PrimarySchoolNode
        from houses.school_gender import SchoolGender
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        seen_acceptable = None

        class AssertingService:
            async def find_nearest(self, postcode, child_age, address="", acceptable=None):
                nonlocal seen_acceptable
                seen_acceptable = acceptable
                from dag.attempt import Attempt

                return Attempt.succeeded(None)

        svc = make_services(school_lookup=AssertingService())
        token = _sp.set(svc)
        try:
            loc = UserInputNode[GeoPoint]("loc_ps_acc", GeoPoint)
            addr = UserInputNode[str]("addr_ps_acc", str)
            PrimarySchoolNode("ps_acc", best_location=loc, best_address=addr, acceptable=("boys", "girls"))
            loc.push(GeoPoint(51.5, -0.37), "test")
            addr.push("31 Isambard Road, Southall, UB2 4GN", "test")
            await flush_processor()
            assert seen_acceptable is not None
            assert set(seen_acceptable) == {SchoolGender.BOYS, SchoolGender.GIRLS}
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_secondary_school_uses_custom_acceptable(self):
        from houses.nodes.schools import SecondarySchoolNode
        from houses.school_gender import SchoolGender
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        seen_acceptable = None

        class AssertingService:
            async def find_nearest(self, postcode, child_age, address="", acceptable=None):
                nonlocal seen_acceptable
                seen_acceptable = acceptable
                from dag.attempt import Attempt

                return Attempt.succeeded(None)

        svc = make_services(school_lookup=AssertingService())
        token = _sp.set(svc)
        try:
            loc = UserInputNode[GeoPoint]("loc_ss_acc", GeoPoint)
            addr = UserInputNode[str]("addr_ss_acc", str)
            SecondarySchoolNode("ss_acc", best_location=loc, best_address=addr, acceptable=("girls",))
            loc.push(GeoPoint(51.5, -0.37), "test")
            addr.push("31 Isambard Road, Southall, UB2 4GN", "test")
            await flush_processor()
            assert seen_acceptable is not None
            assert list(seen_acceptable) == [SchoolGender.GIRLS]
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_primary_school_default_acceptable_is_mixed(self):
        from houses.nodes.schools import PrimarySchoolNode
        from houses.school_gender import SchoolGender
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        seen = None

        class AssertingService:
            async def find_nearest(self, postcode, child_age, address="", acceptable=None):
                nonlocal seen
                seen = acceptable
                from dag.attempt import Attempt

                return Attempt.succeeded(None)

        svc = make_services(school_lookup=AssertingService())
        token = _sp.set(svc)
        try:
            loc = UserInputNode[GeoPoint]("loc_ps_def", GeoPoint)
            addr = UserInputNode[str]("addr_ps_def", str)
            PrimarySchoolNode("ps_def", best_location=loc, best_address=addr)
            loc.push(GeoPoint(51.5, -0.37), "test")
            addr.push("31 Isambard Road, Southall, UB2 4GN", "test")
            await flush_processor()
            assert seen == (SchoolGender.MIXED,)
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_secondary_school_default_acceptable_is_mixed(self):
        from houses.nodes.schools import SecondarySchoolNode
        from houses.school_gender import SchoolGender
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        seen = None

        class AssertingService:
            async def find_nearest(self, postcode, child_age, address="", acceptable=None):
                nonlocal seen
                seen = acceptable
                from dag.attempt import Attempt

                return Attempt.succeeded(None)

        svc = make_services(school_lookup=AssertingService())
        token = _sp.set(svc)
        try:
            loc = UserInputNode[GeoPoint]("loc_ss_def", GeoPoint)
            addr = UserInputNode[str]("addr_ss_def", str)
            SecondarySchoolNode("ss_def", best_location=loc, best_address=addr)
            loc.push(GeoPoint(51.5, -0.37), "test")
            addr.push("31 Isambard Road, Southall, UB2 4GN", "test")
            await flush_processor()
            assert seen == (SchoolGender.MIXED,)
        finally:
            _sp.reset(token)


# ── School model unit tests ─────────────────────────────────────────


class TestSchoolAccepts:
    """School.accepts — checks if a school accepts a child of a given gender."""

    def test_mixed_school_accepts_boys(self):
        from houses.school import School
        from houses.school_gender import SchoolGender

        s = School.from_GIAS_row({"Gender (name)": "Mixed", "TypeOfEstablishment (name)": "Community School"})
        assert s.accepts(SchoolGender.BOYS)

    def test_boys_school_accepts_boys(self):
        from houses.school import School
        from houses.school_gender import SchoolGender

        s = School.from_GIAS_row({"Gender (name)": "Boys", "TypeOfEstablishment (name)": "Academy Converter"})
        assert s.accepts(SchoolGender.BOYS)

    def test_girls_school_rejects_boys(self):
        from houses.school import School
        from houses.school_gender import SchoolGender

        s = School.from_GIAS_row({"Gender (name)": "Girls", "TypeOfEstablishment (name)": "Community School"})
        assert not s.accepts(SchoolGender.BOYS)

    def test_independent_boys_still_accepts_boys(self):
        """fee_paying is separate from gender — a fee-paying boys school still accepts boys."""
        from houses.school import School
        from houses.school_gender import SchoolGender

        s = School.from_GIAS_row({"Gender (name)": "Boys", "TypeOfEstablishment (name)": "Independent School"})
        assert s.accepts(SchoolGender.BOYS)

    def test_mixed_school_accepts_girls(self):
        from houses.school import School
        from houses.school_gender import SchoolGender

        s = School.from_GIAS_row({"Gender (name)": "Mixed"})
        assert s.accepts(SchoolGender.GIRLS)

    def test_mixed_required_for_both_genders(self):
        from houses.school import School
        from houses.school_gender import SchoolGender

        s = School.from_GIAS_row({"Gender (name)": "Mixed"})
        assert s.accepts(SchoolGender.BOYS) and s.accepts(SchoolGender.GIRLS)

    def test_boys_school_rejects_girls(self):
        from houses.school import School
        from houses.school_gender import SchoolGender

        s = School.from_GIAS_row({"Gender (name)": "Boys"})
        assert not s.accepts(SchoolGender.GIRLS)

    def test_unknown_gender_rejects_all(self):
        from houses.school import School
        from houses.school_gender import SchoolGender

        s = School.from_GIAS_row({"Gender (name)": "Not applicable"})
        assert s.gender == SchoolGender.UNKNOWN
        assert not s.accepts(SchoolGender.BOYS)
        assert not s.accepts(SchoolGender.GIRLS)
        assert not s.accepts(SchoolGender.MIXED)


class TestSchoolAcceptsAge:
    """School.accepts_age — checks if a child of a given age can attend."""

    def test_primary_accepts_age_7(self):
        from houses.school import School

        s = School.from_GIAS_row({"PhaseOfEducation (name)": "Primary"})
        assert s.accepts_age(7)

    def test_secondary_accepts_age_13(self):
        from houses.school import School

        s = School.from_GIAS_row({"PhaseOfEducation (name)": "Secondary"})
        assert s.accepts_age(13)

    def test_primary_rejects_teenager(self):
        from houses.school import School

        s = School.from_GIAS_row({"PhaseOfEducation (name)": "Primary"})
        assert not s.accepts_age(13)

    def test_secondary_rejects_young_child(self):
        from houses.school import School

        s = School.from_GIAS_row({"PhaseOfEducation (name)": "Secondary"})
        assert not s.accepts_age(5)

    def test_all_through_accepts_all_ages(self):
        from houses.school import School

        s = School.from_GIAS_row({"PhaseOfEducation (name)": "All-through"})
        assert s.accepts_age(5)
        assert s.accepts_age(11)
        assert s.accepts_age(17)

    def test_not_applicable_falls_back_to_statutory_age(self):
        """Not applicable phase uses StatutoryLowAge/HighAge when available."""
        from houses.school import School

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
        from houses.school import School

        s = School.from_GIAS_row({"PhaseOfEducation (name)": "Not applicable"})
        assert s.accepts_age(10)


class TestSchoolFeePaying:
    """School.fee_paying — detects fee-paying schools from type_of_establishment."""

    def test_independent_school_is_fee_paying(self):
        from houses.school import School

        s = School.from_GIAS_row({"TypeOfEstablishment (name)": "Independent School"})
        assert s.fee_paying

    def test_other_independent_is_fee_paying(self):
        from houses.school import School

        s = School.from_GIAS_row({"TypeOfEstablishment (name)": "Other independent school"})
        assert s.fee_paying

    def test_community_school_not_fee_paying(self):
        from houses.school import School

        s = School.from_GIAS_row({"TypeOfEstablishment (name)": "Community School"})
        assert not s.fee_paying

    def test_academy_converter_not_fee_paying(self):
        from houses.school import School

        s = School.from_GIAS_row({"TypeOfEstablishment (name)": "Academy Converter"})
        assert not s.fee_paying


class TestSchoolCoords:
    """School.coords — parses Latitude/Longitude from a GIAS row."""

    def test_valid_coords(self):
        from houses.geopoint import GeoPoint
        from houses.school import School

        s = School.from_GIAS_row({"CorrectedLatitude": "51.5", "CorrectedLongitude": "-0.13"})
        assert s.coords == GeoPoint(51.5, -0.13)

    def test_missing_lat_returns_none(self):
        from houses.school import School

        s = School.from_GIAS_row({"Longitude": "-0.13"})
        assert s.coords is None

    def test_missing_lng_returns_none(self):
        from houses.school import School

        s = School.from_GIAS_row({"Latitude": "51.5"})
        assert s.coords is None

    def test_empty_strings_returns_none(self):
        from houses.school import School

        s = School.from_GIAS_row({"Latitude": "", "Longitude": ""})
        assert s.coords is None

    def test_zero_coords(self):
        """Zero lat/lng should still return GeoPoint(0, 0)."""
        from houses.geopoint import GeoPoint
        from houses.school import School

        s = School.from_GIAS_row({"CorrectedLatitude": "0", "CorrectedLongitude": "0"})
        assert s.coords == GeoPoint(0.0, 0.0)

    def test_returns_geopoint(self):
        from houses.geopoint import GeoPoint
        from houses.school import School

        s = School.from_GIAS_row({"CorrectedLatitude": "52.2053", "CorrectedLongitude": "0.1218"})
        assert s.coords == GeoPoint(52.2053, 0.1218)


class TestSchoolFromGIASRow:
    """School.from_GIAS_row — parses a GIAS CSV row into a School dataclass."""

    def test_basic_parse(self):
        from houses.school import School
        from houses.school_gender import SchoolGender

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
        from houses.school import School

        s = School.from_GIAS_row({"TypeOfEstablishment (name)": "Independent School"})
        assert s.fee_paying

    def test_missing_name_defaults(self):
        from houses.school import School

        s = School.from_GIAS_row({})
        assert s.name == ""
        assert s.urn == ""

    def test_coords_from_lat_lng(self):
        from houses.geopoint import GeoPoint
        from houses.school import School

        s = School.from_GIAS_row({"CorrectedLatitude": "51.5", "CorrectedLongitude": "-0.13"})
        assert s.coords == GeoPoint(51.5, -0.13)

    def test_missing_coords_is_none(self):
        from houses.school import School

        s = School.from_GIAS_row({})
        assert s.coords is None

    def test_gender_from_raw_string(self):
        from houses.school import School
        from houses.school_gender import SchoolGender

        s = School.from_GIAS_row({"Gender (name)": "Boys"})
        assert s.gender == SchoolGender.BOYS


class TestFindNearestFilters:
    """find_nearest must exclude fee-paying schools, blank names, and filter by gender."""

    @pytest.mark.asyncio
    async def test_excludes_fee_paying_school(self):
        """A fee-paying school should be excluded even if it's the nearest."""
        from houses.school import School
        from houses.schools import SchoolLookupOptions, find_nearest

        fee_paying = School.from_GIAS_row(
            {
                "EstablishmentName": "Expensive School",
                "Gender (name)": "Mixed",
                "PhaseOfEducation (name)": "Primary",
                "TypeOfEstablishment (name)": "Independent School",
                "Latitude": "51.5",
                "Longitude": "-0.1",
                "CorrectedLatitude": "51.5",
                "CorrectedLongitude": "-0.1",
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
                "CorrectedLatitude": "51.501",
                "CorrectedLongitude": "-0.101",
                "Postcode": "SL6 2BB",
            }
        )

        from houses.school_gender import SchoolGender

        result = await find_nearest(
            "SL6 3CC",
            child_age=7,
            options=SchoolLookupOptions(
                acceptable=(SchoolGender.BOYS, SchoolGender.GIRLS, SchoolGender.MIXED),
                load_schools_fn=lambda: [fee_paying, non_fee],
                geocode_fn=_fake_geocode,
                geocode_address_fn=_fake_geocode,
            ),
        )
        assert result.succeeded, "Expected a school, got None"
        school = result.value_or_none()
        assert school is not None
        assert school.name == "Free School", f"Expected Free School, got {school.name}"

    @pytest.mark.asyncio
    async def test_excludes_empty_name_school(self):
        """A school with a blank name should be excluded."""
        from houses.school import School
        from houses.school_gender import SchoolGender
        from houses.schools import SchoolLookupOptions, find_nearest

        unnamed = School.from_GIAS_row(
            {
                "EstablishmentName": "",
                "Gender (name)": "Mixed",
                "PhaseOfEducation (name)": "Primary",
                "TypeOfEstablishment (name)": "Community School",
                "Latitude": "51.5",
                "Longitude": "-0.1",
                "CorrectedLatitude": "51.5",
                "CorrectedLongitude": "-0.1",
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
                "CorrectedLatitude": "51.501",
                "CorrectedLongitude": "-0.101",
                "Postcode": "SL6 2BB",
            }
        )

        result = await find_nearest(
            "SL6 3CC",
            child_age=7,
            options=SchoolLookupOptions(
                acceptable=(SchoolGender.BOYS, SchoolGender.GIRLS, SchoolGender.MIXED),
                load_schools_fn=lambda: [unnamed, named],
                geocode_fn=_fake_geocode,
                geocode_address_fn=_fake_geocode,
            ),
        )
        assert result.succeeded, "Expected a school, got None"
        school = result.value_or_none()
        assert school is not None
        assert school.name == "Has A Name School", (
            f"Expected Has A Name, got {school.name}"
        )

    @pytest.mark.asyncio
    async def test_find_nearest_filters_by_acceptable_boys_only(self):
        """With acceptable=(BOYS,), should return nearest boys school, reject mixed."""
        from houses.school import School
        from houses.school_gender import SchoolGender
        from houses.schools import SchoolLookupOptions, find_nearest

        boys_school = School.from_GIAS_row(
            {
                "EstablishmentName": "Boys Grammar",
                "Gender (name)": "Boys",
                "PhaseOfEducation (name)": "Primary",
                "TypeOfEstablishment (name)": "Community School",
                "Latitude": "51.5",
                "Longitude": "-0.1",
                "CorrectedLatitude": "51.5",
                "CorrectedLongitude": "-0.1",
                "Postcode": "SL6 1AA",
            }
        )
        mixed_school = School.from_GIAS_row(
            {
                "EstablishmentName": "Mixed Primary",
                "Gender (name)": "Mixed",
                "PhaseOfEducation (name)": "Primary",
                "TypeOfEstablishment (name)": "Community School",
                "Latitude": "51.501",
                "Longitude": "-0.099",
                "CorrectedLatitude": "51.501",
                "CorrectedLongitude": "-0.099",
                "Postcode": "SL6 2BB",
            }
        )
        result = await find_nearest(
            "SL6 3CC",
            child_age=7,
            options=SchoolLookupOptions(
                acceptable=(SchoolGender.BOYS,),
                load_schools_fn=lambda: [boys_school, mixed_school],
                geocode_fn=_fake_geocode,
                geocode_address_fn=_fake_geocode,
            ),
        )
        assert result.succeeded
        school = result.value_or_none()
        assert school is not None
        assert school.name == "Boys Grammar"

        # With acceptable=(GIRLS,), neither school matches (one boys, one mixed)
        result = await find_nearest(
            "SL6 3CC",
            child_age=7,
            options=SchoolLookupOptions(
                acceptable=(SchoolGender.GIRLS,),
                load_schools_fn=lambda: [boys_school, mixed_school],
                geocode_fn=_fake_geocode,
                geocode_address_fn=_fake_geocode,
            ),
        )
        assert result.value_or_none() is None

    @pytest.mark.asyncio
    async def test_excludes_special_schools(self):
        """A special school (even at the property) must not be returned as
        the family's primary/secondary — the family needs a mainstream
        school. Regression: 90427107's nearest school for BOTH age 4 and
        age 12 was Chiltern Wood, a community special school (ages 3-19),
        so primary and secondary showed the same special school."""
        from houses.school import School
        from houses.school_gender import SchoolGender
        from houses.schools import SchoolLookupOptions, find_nearest

        special = School.from_GIAS_row(
            {
                "EstablishmentName": "Special Needs School",
                "Gender (name)": "Mixed",
                "PhaseOfEducation (name)": "Not applicable",
                "TypeOfEstablishment (name)": "Community special school",
                "Latitude": "51.5005",
                "Longitude": "-0.1005",
                "CorrectedLatitude": "51.5005",
                "CorrectedLongitude": "-0.1005",
                "Postcode": "SL6 1AA",
                "StatutoryLowAge": "3",
                "StatutoryHighAge": "19",
            }
        )
        mainstream = School.from_GIAS_row(
            {
                "EstablishmentName": "Mainstream Primary",
                "Gender (name)": "Mixed",
                "PhaseOfEducation (name)": "Primary",
                "TypeOfEstablishment (name)": "Community School",
                "Latitude": "51.501",
                "Longitude": "-0.101",
                "CorrectedLatitude": "51.501",
                "CorrectedLongitude": "-0.101",
                "Postcode": "SL6 2BB",
            }
        )

        result = await find_nearest(
            "SL6 3CC",
            child_age=7,
            options=SchoolLookupOptions(
                acceptable=(SchoolGender.BOYS, SchoolGender.GIRLS, SchoolGender.MIXED),
                load_schools_fn=lambda: [special, mainstream],
                geocode_fn=_fake_geocode,
                geocode_address_fn=_fake_geocode,
            ),
        )
        assert result.succeeded, "Expected a school, got None"
        school = result.value_or_none()
        assert school is not None
        assert school.name == "Mainstream Primary", (
            f"Expected Mainstream Primary (special school skipped), got {school.name}"
        )

    @pytest.mark.asyncio
    async def test_find_nearest_filters_by_acceptable_girls_only(self):
        """With acceptable=(GIRLS,), should return nearest girls school only."""
        from houses.school import School
        from houses.school_gender import SchoolGender
        from houses.schools import SchoolLookupOptions, find_nearest

        girls_school = School.from_GIAS_row(
            {
                "EstablishmentName": "Girls Academy",
                "Gender (name)": "Girls",
                "PhaseOfEducation (name)": "Primary",
                "TypeOfEstablishment (name)": "Community School",
                "Latitude": "51.5",
                "Longitude": "-0.1",
                "CorrectedLatitude": "51.5",
                "CorrectedLongitude": "-0.1",
                "Postcode": "SL6 1AA",
            }
        )

        result = await find_nearest(
            "SL6 3CC",
            child_age=7,
            options=SchoolLookupOptions(
                acceptable=(SchoolGender.GIRLS,),
                load_schools_fn=lambda: [girls_school],
                geocode_fn=_fake_geocode,
                geocode_address_fn=_fake_geocode,
            ),
        )
        assert result.succeeded
        school = result.value_or_none()
        assert school is not None
        assert school.name == "Girls Academy"

        # With acceptable=(BOYS,), girls school should be excluded
        result = await find_nearest(
            "SL6 3CC",
            child_age=7,
            options=SchoolLookupOptions(
                acceptable=(SchoolGender.BOYS,),
                load_schools_fn=lambda: [girls_school],
                geocode_fn=_fake_geocode,
                geocode_address_fn=_fake_geocode,
            ),
        )
        assert result.value_or_none() is None, "Girls school should not match BOYS acceptable"

    @pytest.mark.asyncio
    async def test_find_nearest_accepts_all_types(self):
        """With acceptable containing all types, any school should match."""
        from houses.school import School
        from houses.school_gender import SchoolGender
        from houses.schools import SchoolLookupOptions, find_nearest

        girls = School.from_GIAS_row(
            {
                "EstablishmentName": "Girls School",
                "Gender (name)": "Girls",
                "PhaseOfEducation (name)": "Primary",
                "TypeOfEstablishment (name)": "Community School",
                "Latitude": "51.5",
                "Longitude": "-0.1",
                "CorrectedLatitude": "51.5",
                "CorrectedLongitude": "-0.1",
                "Postcode": "SL6 1AA",
            }
        )

        result = await find_nearest(
            "SL6 3CC",
            child_age=7,
            options=SchoolLookupOptions(
                acceptable=(SchoolGender.BOYS, SchoolGender.GIRLS, SchoolGender.MIXED),
                load_schools_fn=lambda: [girls],
                geocode_fn=_fake_geocode,
                geocode_address_fn=_fake_geocode,
            ),
        )
        assert result.succeeded
        result = await find_nearest(
            "SL6 3CC",
            child_age=7,
            options=SchoolLookupOptions(
                acceptable=(SchoolGender.BOYS,),
                load_schools_fn=lambda: [girls],
                geocode_fn=_fake_geocode,
                geocode_address_fn=_fake_geocode,
            ),
        )
        assert result.value_or_none() is None

    @pytest.mark.asyncio
    async def test_skips_school_without_coords_no_geocode(self):
        """find_nearest must skip schools with coords=None without
        calling geocode (no API calls at query time)."""
        from dag.attempt import Attempt
        from houses.geopoint import GeoPoint
        from houses.school import School
        from houses.school_gender import SchoolGender
        from houses.schools import SchoolLookupOptions, find_nearest

        geocode_called = False

        async def fake_geocode(*_):
            nonlocal geocode_called
            geocode_called = True
            return Attempt.succeeded(GeoPoint(51.5, -0.13))

        no_coords = School(
            urn="1",
            name="No Coords School",
            phase="Primary",
            gender=SchoolGender.MIXED,
            type_of_establishment="Community School",
            postcode="UB2 4RP",
            website="",
            ofsted_rating="Good",
            inspection_year="2022",
            coords=None,
            _postcode_centroid=GeoPoint(51.5, -0.13),
            statutory_low_age=4,
            statutory_high_age=11,
        )
        has_coords = School(
            urn="2",
            name="Has Coords School",
            phase="Primary",
            gender=SchoolGender.MIXED,
            type_of_establishment="Community School",
            postcode="UB2 4HT",
            website="",
            ofsted_rating="Good",
            inspection_year="2022",
            coords=GeoPoint(52.0, -0.13),
            statutory_low_age=4,
            statutory_high_age=11,
        )

        result = await find_nearest(
            "51.5,-0.13",
            child_age=7,
            options=SchoolLookupOptions(
                acceptable=(SchoolGender.MIXED,),
                load_schools_fn=lambda: [no_coords, has_coords],
                geocode_fn=fake_geocode,
                geocode_address_fn=fake_geocode,
            ),
        )
        assert result.pending, (
            "When a school matching filters lacks coords, must return pending "
            "(incomplete data, cannot give definitive answer)"
        )
        assert not geocode_called, "find_nearest must NOT geocode school postcodes at query time"


class TestSchoolErrorPropagation:
    """School nodes must propagate the lookup's real error, not collapse
    it into a generic 'no school found'.

    Regression: find_nearest returned succeeded(None) when geocoding
    failed, and the node returned 'no primary school found' — hiding the
    geocode failure from the frontend.
    """

    @pytest.mark.asyncio
    async def test_propagates_geocode_failure(self):
        from houses.nodes.schools import PrimarySchoolNode
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        class _FailingLookup:
            async def find_nearest(self, postcode, child_age, address="", acceptable=()):
                return Attempt.impossible("geocode failed: postcode not found (404)")

            async def school_commute(self, postcode, school):
                return None

        token = _sp.set(make_services(school_lookup=_FailingLookup()))
        try:
            loc = UserInputNode[GeoPoint]("loc_pe1", GeoPoint)
            addr = UserInputNode[str]("addr_pe1", str)
            node = PrimarySchoolNode("pe1", best_location=loc, best_address=addr)
            loc.push(GeoPoint(51.5, -0.1), "test")
            addr.push("10 High St, London, SW1V 2QQ", "test")
            await flush_processor()
            a = await node.attempt()
            assert not a.succeeded
            assert "postcode not found (404)" in a.error, f"Expected geocode reason, got: {a.error}"
            assert "no primary school found" not in a.error
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_no_school_has_sensible_message(self):
        from houses.nodes.schools import PrimarySchoolNode
        from houses.services_provider import _request_services as _sp
        from tests.helpers import FakeSchoolLookup, make_services

        token = _sp.set(make_services(school_lookup=FakeSchoolLookup(school=None)))
        try:
            loc = UserInputNode[GeoPoint]("loc_pe2", GeoPoint)
            addr = UserInputNode[str]("addr_pe2", str)
            node = PrimarySchoolNode("pe2", best_location=loc, best_address=addr)
            loc.push(GeoPoint(51.5, -0.1), "test")
            addr.push("10 High St, London, SW1V 2QQ", "test")
            await flush_processor()
            a = await node.attempt()
            assert not a.succeeded
            assert "no primary school found" in a.error
        finally:
            _sp.reset(token)
