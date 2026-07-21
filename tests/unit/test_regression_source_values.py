"""Regression: after removing dead source_values table, seed_registry_from_sheet()
must push source values for every property so the DAG computes derived nodes.
"""

from __future__ import annotations

import pytest

from dag.scheduler import flush_processor
from houses.nodes.bootstrap import bootstrap_from_row
from houses.nodes.property import PropertyNodes
from houses.property_registry import register_property
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
def _mock(monkeypatch):
    from money import Money
    from pint import Quantity

    from dag.attempt import Attempt
    from houses.model.domain import Commute, Person, PlaceOfInterest
    from houses.services_provider import _request_services as _sp

    svc = make_services()
    token = _sp.set(svc)

    canned = Commute(
        person=Person(name="T", has_car=True),
        label="Test",
        destination=PlaceOfInterest(label="D", postcode="SW1V 2QQ"),
        duration=Quantity(30, "minute"),
        daily_cost=Money("5.0", "GBP"),
        mode="transit",
    )

    async def fake_route(*_, **__):
        return Attempt.succeeded(canned)

    svc.commute_router.route = fake_route
    monkeypatch.setattr("houses.routing.CommuteRouter._google_route_commute", fake_route)

    yield
    _sp.reset(token)


@pytest.mark.asyncio
async def test_push_happens_without_old_table():
    """seed_registry_from_sheet() no longer checks source_values (dead table).
    Every property gets source values pushed, triggering DAG computation."""
    rid = "push_test"
    prop = PropertyNodes(rid)
    source_dict = {
        "rightmove_address": prop.rightmove_address,
        "rightmove_url": prop.rightmove_url,
        "rightmove_bedrooms": prop.rightmove_bedrooms,
        "rightmove_price": prop.rightmove_price,
        "rightmove_location": prop.rightmove_location,
        "precise_location": prop.precise_location,
        "corrected_address": prop.corrected_address,
        "user_entered_address": prop.user_entered_address,
        "postcode": prop.postcode,
    }
    row = {
        "Rightmove ID": rid,
        "Address": "1 Test Road, TE1 1ST",
        "Postcode": "TE1 1ST",
        "Rightmove URL": "https://rightmove.co.uk/001",
        "Bedrooms": "3",
        "Price (£)": "500000",
        "Approx Latitude (est)": "51.5",
        "Approx Longitude (est)": "-0.1",
    }

    # This must happen — no old table to block it
    bootstrap_from_row(row, source_dict)

    await flush_processor()
    await flush_processor()

    register_property(rid, prop)
    sm = await prop.to_json_summary()
    commutes = sm.get("commutes", {})
    assert len(commutes) > 0, "No commute selectors"
    for key, cd in commutes.items():
        c = cd["commute"]
        assert c.get("succeeded"), (
            f"Commute {key!r} should succeed after push. pending={c.get('pending')} impossible={c.get('impossible')}"
        )
