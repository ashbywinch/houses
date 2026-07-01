from __future__ import annotations

import pytest

from dag.attempt import Provenance
from dag.source_node import SourceNode
from houses.geo import GeoPoint


@pytest.fixture(autouse=True)
def _fake_svc():
    import houses.context as ctx
    from tests.helpers import make_services
    token = ctx._request_services.set(make_services())
    yield
    ctx._request_services.reset(token)


@pytest.mark.asyncio
async def test_primary_school_impossible_without_location():
    from houses.nodes.schools import PrimarySchoolNode

    loc = SourceNode[GeoPoint]("loc_ps", GeoPoint)
    addr = SourceNode[str]("addr_ps", str)
    addr.push("10 High St, SW1V 2QQ", Provenance("test"))
    node = PrimarySchoolNode("ps", best_location=loc, best_address=addr)
    a = await node.attempt()
    assert not a.is_succeeded


@pytest.mark.asyncio
async def test_secondary_school_impossible_without_location():
    from houses.nodes.schools import SecondarySchoolNode

    loc = SourceNode[GeoPoint]("loc_ss", GeoPoint)
    addr = SourceNode[str]("addr_ss", str)
    addr.push("10 High St, SW1V 2QQ", Provenance("test"))
    node = SecondarySchoolNode("ss", best_location=loc, best_address=addr)
    a = await node.attempt()
    assert not a.is_succeeded


@pytest.mark.asyncio
async def test_primary_school_impossible_without_address():
    from houses.nodes.schools import PrimarySchoolNode

    loc = SourceNode[GeoPoint]("loc_ps2", GeoPoint)
    loc.push(GeoPoint(51.5, -0.1), Provenance("test"))
    addr = SourceNode[str]("addr_ps2", str)
    node = PrimarySchoolNode("ps2", best_location=loc, best_address=addr)
    a = await node.attempt()
    assert not a.is_succeeded


@pytest.mark.asyncio
async def test_secondary_school_impossible_without_address():
    from houses.nodes.schools import SecondarySchoolNode

    loc = SourceNode[GeoPoint]("loc_ss2", GeoPoint)
    loc.push(GeoPoint(51.5, -0.1), Provenance("test"))
    addr = SourceNode[str]("addr_ss2", str)
    node = SecondarySchoolNode("ss2", best_location=loc, best_address=addr)
    a = await node.attempt()
    assert not a.is_succeeded


def test_distance_km_with_geopoint_coords():
    from houses.geo import GeoPoint
    from houses.nodes.schools import _distance_km
    from houses.schools import School, SchoolGender

    school = School(
        urn="123", name="Test", phase="primary",
        gender=SchoolGender.MIXED, type_of_establishment="community school",
        postcode="SW1V 2QQ", website="", ofsted_rating="Good",
        inspection_year="2022", coords=GeoPoint(51.5, -0.1),
        statutory_low_age=None, statutory_high_age=None,
    )
    loc = GeoPoint(51.4, -0.2)
    km = _distance_km(loc, school)
    assert km > 0


def test_distance_km_none_coords():
    from houses.geo import GeoPoint
    from houses.nodes.schools import _distance_km
    from houses.schools import School, SchoolGender

    school = School(
        urn="456", name="No Coords", phase="secondary",
        gender=SchoolGender.MIXED, type_of_establishment="academy",
        postcode="EC3A 7LP", website="", ofsted_rating="Outstanding",
        inspection_year="2023", coords=None,
        statutory_low_age=None, statutory_high_age=None,
    )
    loc = GeoPoint(51.5, -0.1)
    assert _distance_km(loc, school) == 0.0


@pytest.mark.asyncio
async def test_school_location_node_fails_without_school():
    from dag.source_node import SourceNode
    from houses.nodes.schools import SchoolLocationNode

    school = SourceNode[dict]("sn", dict)
    node = SchoolLocationNode("sln", school_node=school)
    a = await node.attempt()
    assert not a.is_succeeded
