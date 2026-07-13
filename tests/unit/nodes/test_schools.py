from __future__ import annotations

import pytest

from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint


@pytest.fixture(autouse=True)
def _fake_svc():
    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services
    token = _sp.set(make_services())
    yield
    _sp.reset(token)


@pytest.mark.asyncio
async def test_primary_school_impossible_without_location():
    from houses.nodes.schools import PrimarySchoolNode

    loc = UserInputNode[GeoPoint]("loc_ps", GeoPoint)
    addr = UserInputNode[str]("addr_ps", str)
    addr.push("10 High St, SW1V 2QQ", "test")
    node = PrimarySchoolNode("ps", best_location=loc, best_address=addr)
    a = await node.attempt()
    assert not a.succeeded


@pytest.mark.asyncio
async def test_secondary_school_impossible_without_location():
    from houses.nodes.schools import SecondarySchoolNode

    loc = UserInputNode[GeoPoint]("loc_ss", GeoPoint)
    addr = UserInputNode[str]("addr_ss", str)
    addr.push("10 High St, SW1V 2QQ", "test")
    node = SecondarySchoolNode("ss", best_location=loc, best_address=addr)
    a = await node.attempt()
    assert not a.succeeded


@pytest.mark.asyncio
async def test_primary_school_impossible_without_address():
    from houses.nodes.schools import PrimarySchoolNode

    loc = UserInputNode[GeoPoint]("loc_ps2", GeoPoint)
    loc.push(GeoPoint(51.5, -0.1), "test")
    addr = UserInputNode[str]("addr_ps2", str)
    node = PrimarySchoolNode("ps2", best_location=loc, best_address=addr)
    a = await node.attempt()
    assert not a.succeeded


@pytest.mark.asyncio
async def test_secondary_school_impossible_without_address():
    from houses.nodes.schools import SecondarySchoolNode

    loc = UserInputNode[GeoPoint]("loc_ss2", GeoPoint)
    loc.push(GeoPoint(51.5, -0.1), "test")
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
