"""Detail endpoint must include stamp_duty in affordability section."""

from __future__ import annotations

import pytest

from houses.geo import GeoPoint
from houses.nodes.property import PropertyNodes
from houses.property_registry import _registry, register_property
from tests.helpers import make_services


@pytest.fixture(autouse=True)
def _fresh_db():
    import sqlite3

    import dag.persistence as per
    saved = per._get_db
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    per._get_db = lambda: conn
    per.init_db()
    yield
    per._get_db = saved

@pytest.fixture(autouse=True)
def _clear():
    _registry.clear()
    yield
    _registry.clear()

@pytest.fixture(autouse=True)
def _mock():
    from houses.services_provider import _request_services as _sp
    token = _sp.set(make_services())
    yield
    _sp.reset(token)


@pytest.mark.asyncio
async def test_detail_includes_stamp_duty():
    """Property detail must include stamp_duty in affordability."""
    rid = "sd_test"
    prop = PropertyNodes(rid)
    prop.rightmove_address.push("1 Test Road, TE1 1ST", "Rightmove")
    prop.rightmove_url.push("https://rightmove.co.uk/001", "Browser")
    prop.rightmove_bedrooms.push("3", "Rightmove")
    prop.rightmove_price.push("795000", "Rightmove")
    prop.rightmove_location.push(GeoPoint(51.5, -0.1), "Rightmove map")
    from dag.derived_node import flush_processor
    await flush_processor()
    await flush_processor()
    register_property(rid, prop)

    detail = await prop.to_json_detail()
    aff = detail.get("affordability", {})

    assert "stamp_duty" in aff, (
        f"stamp_duty missing from affordability. Got keys: {list(aff.keys())}"
    )
    sd = aff["stamp_duty"]
    assert sd.get("succeeded"), f"stamp_duty not succeeded: {sd}"
    val = sd.get("value")
    assert val is not None, f"stamp_duty has no value: {sd}"
    assert val > 0, f"stamp_duty should be positive for a £795k property, got {val}"
