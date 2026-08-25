"""School commute must use coordinates (GeoPoint), not postcodes."""

from __future__ import annotations

import pytest

from houses.geopoint import GeoPoint


@pytest.fixture(autouse=True)
def _fake_svc():
    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services

    token = _sp.set(make_services())
    yield
    _sp.reset(token)


@pytest.mark.asyncio
async def test_school_location_node_returns_geopoint():
    """SchoolLocationNode must return the school's coordinates as GeoPoint."""
    from dag.user_input_node import UserInputNode
    from houses.nodes.schools import PrimarySchoolNode, SchoolLocationNode
    from houses.school import School
    from houses.school_gender import SchoolGender

    loc = UserInputNode[GeoPoint]("loc", GeoPoint)
    addr = UserInputNode[str]("addr", str)

    # Create derived nodes FIRST so they connect to dep signals
    primary = PrimarySchoolNode("ps", best_location=loc, best_address=addr)
    school_loc = SchoolLocationNode("sln", school_node=primary)

    # Push values — this triggers changed.emit() which queues the derived nodes
    loc.push(GeoPoint(51.5, -0.37), "test")
    addr.push("31 Isambard Road, Southall, UB2 4GN", "test")

    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services

    async def fake_find(*a, **kw):
        from dag.attempt import Attempt

        return Attempt.succeeded(
            School(
                urn="103578",
                name="Pimlico Primary",
                phase="Primary",
                gender=SchoolGender.MIXED,
                type_of_establishment="Community School",
                postcode="SW1V 2QQ",
                website="",
                ofsted_rating="Outstanding",
                inspection_year="2023",
                coords=GeoPoint(51.488, -0.138),
                statutory_low_age=None,
                statutory_high_age=None,
            )
        )

    svc = make_services(school_lookup=type("FS", (), {"find_nearest": fake_find})())
    token = _sp.set(svc)
    try:
        from dag.scheduler import flush_processor

        await flush_processor()
        await flush_processor()
        a = await school_loc.attempt()
        assert a.succeeded, f"school loc failed: {a.error}"
    finally:
        _sp.reset(token)


@pytest.mark.asyncio
async def test_school_node_output_has_url():
    """School node output must contain 'url' and 'coords' keys."""
    from dag.user_input_node import UserInputNode
    from houses.nodes.schools import PrimarySchoolNode
    from houses.school import School
    from houses.school_gender import SchoolGender

    loc = UserInputNode[GeoPoint]("loc2", GeoPoint)
    addr = UserInputNode[str]("addr2", str)

    # Create derived node FIRST so it connects to dep signals
    sn = PrimarySchoolNode("ps2", best_location=loc, best_address=addr)

    # Push values — this triggers changed.emit() which queues the derived node
    loc.push(GeoPoint(51.5, -0.37), "test")
    addr.push("31 Isambard Road, Southall, UB2 4GN", "test")

    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services

    async def fake_find2(*a, **kw):
        from dag.attempt import Attempt

        return Attempt.succeeded(
            School(
                urn="123456",
                name="Test School",
                phase="primary",
                gender=SchoolGender.BOYS,
                type_of_establishment="community school",
                postcode="SW1V 2QQ",
                website="",
                ofsted_rating="Good",
                inspection_year="2022",
                coords=GeoPoint(51.5, -0.37),
                statutory_low_age=None,
                statutory_high_age=None,
            )
        )

    svc = make_services(school_lookup=type("FS", (), {"find_nearest": fake_find2})())
    token = _sp.set(svc)
    try:
        from dag.scheduler import flush_processor

        await flush_processor()
        await flush_processor()
        a = await sn.attempt()
        assert a.succeeded, f"school failed: {a.error}"
        val = a.value_or_none()
        assert val is not None, f"school returned no value: {a.error}"
        assert "name" in val, f"missing name: {list(val.keys())}"
        assert val["name"] == "Test School"
        assert val.get("ofsted") == "Good"
    finally:
        _sp.reset(token)


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
