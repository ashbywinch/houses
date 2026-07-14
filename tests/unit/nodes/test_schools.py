from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from houses.school import School

from dag.derived_node import flush_processor
from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint


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
    await flush_processor()
    node = PrimarySchoolNode("ps", best_location=loc, best_address=addr)
    a = await node.attempt()
    assert not a.succeeded


@pytest.mark.asyncio
async def test_secondary_school_impossible_without_location():
    from houses.nodes.schools import SecondarySchoolNode

    loc = UserInputNode[GeoPoint]("loc_ss", GeoPoint)
    addr = UserInputNode[str]("addr_ss", str)
    addr.push("10 High St, SW1V 2QQ", "test")
    await flush_processor()
    await flush_processor()
    node = SecondarySchoolNode("ss", best_location=loc, best_address=addr)
    a = await node.attempt()
    assert not a.succeeded


@pytest.mark.asyncio
async def test_primary_school_impossible_without_address():
    from houses.nodes.schools import PrimarySchoolNode

    loc = UserInputNode[GeoPoint]("loc_ps2", GeoPoint)
    loc.push(GeoPoint(51.5, -0.1), "test")
    await flush_processor()
    await flush_processor()
    addr = UserInputNode[str]("addr_ps2", str)
    node = PrimarySchoolNode("ps2", best_location=loc, best_address=addr)
    a = await node.attempt()
    assert not a.succeeded


@pytest.mark.asyncio
async def test_secondary_school_impossible_without_address():
    from houses.nodes.schools import SecondarySchoolNode

    loc = UserInputNode[GeoPoint]("loc_ss2", GeoPoint)
    loc.push(GeoPoint(51.5, -0.1), "test")
    await flush_processor()
    await flush_processor()
    addr = UserInputNode[str]("addr_ss2", str)
    node = SecondarySchoolNode("ss2", best_location=loc, best_address=addr)
    a = await node.attempt()
    assert not a.succeeded


@pytest.mark.asyncio
async def test_school_location_node_fails_without_school():
    from dag.user_input_node import UserInputNode
    from houses.nodes.schools import SchoolLocationNode

    school = UserInputNode[dict]("sn", dict)
    node = SchoolLocationNode("sln", school_node=school)
    a = await node.attempt()
    assert not a.succeeded

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
        await flush_processor()
        a = await node.attempt()
        assert not a.succeeded
        assert "no primary school found" in a.error
    finally:
        _sp.reset(token)


class TestSchoolGenderFiltering:
    """School.accepts_any filters schools by acceptable genders."""

    def _make_school(self, gender: str) -> School:  # noqa: F821
        from houses.geo import GeoPoint
        from houses.school import School
        from houses.school_gender import SchoolGender
        return School(
            urn="test", name="Test School", phase="primary",
            gender=SchoolGender(gender),
            type_of_establishment="community school",
            postcode="SW1V 2QQ", website="",
            ofsted_rating="Good", inspection_year="2022",
            coords=GeoPoint(51.5, -0.13), statutory_low_age=None,
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
                return None

        svc = make_services(school_lookup=AssertingService())
        token = _sp.set(svc)
        try:
            loc = UserInputNode[GeoPoint]("loc_ps_acc", GeoPoint)
            addr = UserInputNode[str]("addr_ps_acc", str)
            PrimarySchoolNode("ps_acc", best_location=loc, best_address=addr,
                                     acceptable=("boys", "girls"))
            loc.push(GeoPoint(51.5, -0.37), "test")
            addr.push("31 Isambard Road, Southall, UB2 4GN", "test")
            await flush_processor()
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
                return None

        svc = make_services(school_lookup=AssertingService())
        token = _sp.set(svc)
        try:
            loc = UserInputNode[GeoPoint]("loc_ss_acc", GeoPoint)
            addr = UserInputNode[str]("addr_ss_acc", str)
            SecondarySchoolNode("ss_acc", best_location=loc, best_address=addr,
                                       acceptable=("girls",))
            loc.push(GeoPoint(51.5, -0.37), "test")
            addr.push("31 Isambard Road, Southall, UB2 4GN", "test")
            await flush_processor()
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
                return None

        svc = make_services(school_lookup=AssertingService())
        token = _sp.set(svc)
        try:
            loc = UserInputNode[GeoPoint]("loc_ps_def", GeoPoint)
            addr = UserInputNode[str]("addr_ps_def", str)
            PrimarySchoolNode("ps_def", best_location=loc, best_address=addr)
            loc.push(GeoPoint(51.5, -0.37), "test")
            addr.push("31 Isambard Road, Southall, UB2 4GN", "test")
            await flush_processor()
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
                return None

        svc = make_services(school_lookup=AssertingService())
        token = _sp.set(svc)
        try:
            loc = UserInputNode[GeoPoint]("loc_ss_def", GeoPoint)
            addr = UserInputNode[str]("addr_ss_def", str)
            SecondarySchoolNode("ss_def", best_location=loc, best_address=addr)
            loc.push(GeoPoint(51.5, -0.37), "test")
            addr.push("31 Isambard Road, Southall, UB2 4GN", "test")
            await flush_processor()
            await flush_processor()
            assert seen == (SchoolGender.MIXED,)
        finally:
            _sp.reset(token)
