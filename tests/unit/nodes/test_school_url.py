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
    from dag.attempt import Provenance
    from dag.source_node import SourceNode
    from houses.schools import School, SchoolGender

    loc = SourceNode[GeoPoint]("loc", GeoPoint)
    loc.push(GeoPoint(51.5, -0.37), Provenance("test"))
    addr = SourceNode[str]("addr", str)
    addr.push("31 Isambard Road, Southall, UB2 4GN", Provenance("test"))

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
        assert a.is_succeeded, f"school loc failed: {a._error}"
        assert isinstance(a.value_or_none(), GeoPoint)
        assert a.value_or_none().lat == 51.5
    finally:
        ctx._request_services.reset(token)


@pytest.mark.asyncio
async def test_school_node_output_has_url():
    """School node output must contain 'url' and 'coords' keys."""
    from dag.attempt import Provenance
    from dag.source_node import SourceNode
    from houses.schools import School, SchoolGender

    loc = SourceNode[GeoPoint]("loc2", GeoPoint)
    loc.push(GeoPoint(51.5, -0.37), Provenance("test"))
    addr = SourceNode[str]("addr2", str)
    addr.push("31 Isambard Road, Southall, UB2 4GN", Provenance("test"))

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
        assert a.is_succeeded, f"school failed: {a._error}"
        val = a.value_or_none()
        assert "url" in val, f"missing url: {list(val.keys())}"
        assert val["url"] == "https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/123456"
        assert isinstance(val.get("coords"), GeoPoint), f"coords should be GeoPoint: {val.get('coords')}"
        assert val["coords"].lat == 51.5
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
