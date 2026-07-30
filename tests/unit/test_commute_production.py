"""Verify production API response shape matches frontend types.

Uses the real production code path: seed sources → flush_processor() →
to_json_summary().  No reliance on the conftest _flushing_attempt patch.
"""

from __future__ import annotations

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.scheduler import flush_processor
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
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
    from houses.tfl_client import TflClient

    class _SuccessPlanner:
        async def walk_route(self, origin, destination, max_walk):
            return Attempt.succeeded(
                Commute(
                    person=Person(name="Test", has_car=False),
                    label="Walk",
                    destination=PlaceOfInterest(label="Dest", address=str(destination)),
                    duration=Quantity(30, "minute"),
                    daily_cost=Money("0", "GBP"),
                )
            )

        async def drive_route(self, origin, destination):
            return Attempt.succeeded(
                Commute(
                    person=Person(name="Test", has_car=True),
                    label="Drive",
                    destination=PlaceOfInterest(label="Dest", address=str(destination)),
                    duration=Quantity(20, "minute"),
                    daily_cost=Money("5.0", "GBP"),
                )
            )

    from money import Money

    canned = Commute(
        person=Person(name="Test", has_car=False),
        label="Test",
        destination=PlaceOfInterest(label="Dest", address="SW1V 2QQ"),
        duration=Quantity(30, "minute"),
        daily_cost=Money("5.0", "GBP"),
        mode="transit",
    )

    async def mock_plan(self):
        return Attempt.succeeded(canned)

    TflClient.plan = mock_plan

    svc = make_services(route_planner=_SuccessPlanner())
    token = _sp.set(svc)
    yield
    _sp.reset(token)


def _commute_duration(commute: dict) -> int | None:
    """Mimics frontend PropertyCard.vue commuteDuration()."""
    if not commute.get("succeeded"):
        return None
    val = commute.get("value")
    if not isinstance(val, dict):
        return None
    dur = val.get("duration")
    if not isinstance(dur, dict):
        return None
    v = dur.get("value")
    return round(v) if isinstance(v, (int, float)) else None


@pytest.mark.asyncio
async def test_production_commute_flow():
    """Full production flow: seed → processor → API response → frontend render."""
    rid = "prod_flow_test"
    prop = PropertyNodes(rid)
    prop.rightmove_address.push("1 Test Road, TE1 1ST", "Rightmove")
    prop.rightmove_url.push("https://rightmove.co.uk/001", "Browser")
    prop.rightmove_bedrooms.push("3", "Rightmove")
    prop.rightmove_price.push(Money("500000", "GBP"), "Rightmove")
    prop.rightmove_location.push(GeoPoint(51.5, -0.1), "Rightmove map")
    prop.postcode.push("TE1 1ST", "test")

    # Run processor — this is what the production _processor task does
    await flush_processor()
    await flush_processor()  # second pass for transitive deps

    summary = await prop.to_json_summary()

    # 1. There must be commute selectors
    commutes = summary.get("commutes", {})
    assert len(commutes) > 0, "No commute selectors in summary"

    # 2. Every commute must be parseable by the frontend
    for key, cd in commutes.items():
        c = cd["commute"]
        dur = _commute_duration(c)

        assert dur is not None, (
            f"Commute {key!r} frontend cannot parse duration. "
            f"succeeded={c.get('succeeded')} pending={c.get('pending')} "
            f"impossible={c.get('impossible')} error={c.get('error')} "
            f"value={c.get('value')!r}"
        )
        assert isinstance(dur, int), f"Duration must be int, got {type(dur).__name__}"
        assert dur > 0, f"Duration must be positive, got {dur}"


@pytest.mark.asyncio
async def test_school_commutes_resolve():
    """George's school commutes must succeed through the production flush_processor path.

    School commutes use SchoolLocationNode (a DerivedNode) instead of a
    plain UserInputNode[str] for the POI source.  This means they depend
    on PrimarySchoolNode / SecondarySchoolNode resolving first, which in
    turn depend on BestLocationNode.  The full chain must cascade through
    ``flush_processor``.
    """
    rid = "school_test"
    prop = PropertyNodes(rid)
    prop.rightmove_address.push("1 Test Road, TE1 1ST", "Rightmove")
    prop.rightmove_url.push("https://rightmove.co.uk/001", "Browser")
    prop.rightmove_bedrooms.push("3", "Rightmove")
    prop.rightmove_price.push(Money("500000", "GBP"), "Rightmove")
    prop.postcode.push("TE1 1ST", "test")
    register_property(rid, prop)

    await flush_processor()
    await flush_processor()

    summary = await prop.to_json_summary()

    # 1. School data must be present and succeeded
    schools = summary.get("schools", {})
    for phase in ("primary", "secondary"):
        s = schools.get(phase, {}).get("school", {})
        assert s.get("succeeded"), (
            f"{phase} school should succeed.  "
            f"pending={s.get('pending')} impossible={s.get('impossible')} "
            f"error={s.get('error')}"
        )

    # 2. George's school commutes must exist and succeed
    commutes = summary.get("commutes", {})
    for key in ("George/Primary School", "George/Secondary School"):
        assert key in commutes, f"Missing school commute: {key!r}"
        c = commutes[key]["commute"]
        assert c.get("succeeded"), (
            f"{key} should succeed.  pending={c.get('pending')} impossible={c.get('impossible')} error={c.get('error')}"
        )
        # Verify is_child flag
        assert c.get("is_child") is True, f"{key} must have is_child=True"

        # Verify commute data is parseable by frontend
        dur = _commute_duration(c)
        assert dur is not None, (
            f"{key} duration not parseable.  succeeded={c.get('succeeded')} value={c.get('value')!r}"
        )
        assert isinstance(dur, int), f"{key} duration must be int, got {type(dur).__name__}"
        assert dur > 0, f"{key} duration must be positive, got {dur}"

    # 3. Adult commutes must also still succeed
    for key in ("Simon/Pimlico", "Simon/Bracknell", "Lorena/Aldgate"):
        assert key in commutes, f"Missing adult commute: {key!r}"
        c = commutes[key]["commute"]
        assert c.get("succeeded"), f"{key} should also succeed.  error={c.get('error')}"
        assert c.get("is_child") is False, f"{key} must have is_child=False"
