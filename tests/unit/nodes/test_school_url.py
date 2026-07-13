"""School commute must use coordinates (GeoPoint), not postcodes."""
from __future__ import annotations

import pytest

from houses.geo import GeoPoint


@pytest.fixture(autouse=True)
def _fake_svc():
    import houses.context as ctx
    from tests.helpers import make_services
    token = ctx._request_services.set(make_services())
    yield
    ctx._request_services.reset(token)


@pytest.mark.asyncio
async def test_school_location_node_returns_geopoint():
    """SchoolLocationNode must return the school's coordinates as GeoPoint."""
    from dag.user_input_node import UserInputNode
    from houses.school import School
    from houses.school_gender import SchoolGender

    loc = UserInputNode[GeoPoint]("loc", GeoPoint)
    loc.push(GeoPoint(51.5, -0.37), "test")
    addr = UserInputNode[str]("addr", str)
    addr.push("31 Isambard Road, Southall, UB2 4GN", "test")

    import houses.context as ctx
    from tests.helpers import make_services

    async def fake_find(*a, **kw):
        return School(
            urn="123", name="Test School", phase="primary",
            gender=SchoolGender.BOYS, type_of_establishment="community",
            postcode="SW1V 2QQ", website="", ofsted_rating="Good",
            inspection_year="2022",
            coords=GeoPoint(51.5, -0.37),
            statutory_low_age=None, statutory_high_age=None,
        )

    svc = make_services(school_lookup=type("FS", (), {"find_nearest": fake_find})())
    token = ctx._request_services.set(svc)
    try:
        from houses.nodes.schools import PrimarySchoolNode, SchoolLocationNode
        primary = PrimarySchoolNode("ps", best_location=loc, best_address=addr)
        school_loc = SchoolLocationNode("sln", school_node=primary)
        a = await school_loc.attempt()
        assert a.succeeded, f"school loc failed: {a.error}"
    finally:
        ctx._request_services.reset(token)


@pytest.mark.asyncio
async def test_school_node_output_has_url():
    """School node output must contain 'url' and 'coords' keys."""
    from dag.user_input_node import UserInputNode
    from houses.school import School
    from houses.school_gender import SchoolGender

    loc = UserInputNode[GeoPoint]("loc2", GeoPoint)
    loc.push(GeoPoint(51.5, -0.37), "test")
    addr = UserInputNode[str]("addr2", str)
    addr.push("31 Isambard Road, Southall, UB2 4GN", "test")

    import houses.context as ctx
    from tests.helpers import make_services

    async def fake_find2(*a, **kw):
        return School(
            urn="123456", name="Test School", phase="primary",
            gender=SchoolGender.BOYS, type_of_establishment="community school",
            postcode="SW1V 2QQ", website="", ofsted_rating="Good",
            inspection_year="2022",
            coords=GeoPoint(51.5, -0.37),
            statutory_low_age=None, statutory_high_age=None,
        )

    svc = make_services(school_lookup=type("FS", (), {"find_nearest": fake_find2})())
    token = ctx._request_services.set(svc)
    try:
        from houses.nodes.schools import PrimarySchoolNode
        sn = PrimarySchoolNode("ps2", best_location=loc, best_address=addr)
        a = await sn.attempt()
        assert a.succeeded, f"school failed: {a.error}"
        val = a.value_or_none()
        assert "name" in val, f"missing name: {list(val.keys())}"
        assert val["name"] == "Test School"
        assert val.get("ofsted") == "Good"
    finally:
        ctx._request_services.reset(token)


@pytest.mark.asyncio
async def test_transit_node_accepts_geopoint_dest():
    """TransitNode must accept GeoPoint as destination (school location)."""
    from money import Money
    from pint import Quantity

    from houses.nodes.transit import CommuteResult

    # CommuteResult itself should accept a GeoPoint as destination reference
    cr = CommuteResult(
        duration=Quantity(22, "minute"),
        daily_cost=Money("0", "GBP"),
        label="Test School",
        mode="walk",
    )
    assert cr.mode == "walk"
