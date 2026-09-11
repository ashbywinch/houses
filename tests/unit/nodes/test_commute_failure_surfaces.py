"""A commute computation that ERRORS must surface — on its own row and on
the household total — never be papered over.

Live 2026-09-10: a wrapper node turned a failed commute pipeline into
``succeeded`` with a null value, so the person's cost silently dropped out
of the monthly total and their row rendered blank.  A total that quietly
omits a cost is worse than one that cannot be computed: an error propagates
so somebody can see it and fix it.

The contract this pins:

- a commute that could not be computed is reported as failed, with its
  reason;
- no entry reports ``succeeded`` with a null value (the papering-over
  shape — a failure dressed as an answer);
- when an entry cannot be computed, the household total cannot be
  computed either — it must not report a smaller number built from
  whatever happened to price.
"""

from __future__ import annotations

import pytest
from money import Money

import dag.user_input_node  # noqa: F401 — register Money/Quantity pydantic schemas
from dag.scheduler import flush_processor
from houses.geopoint import GeoPoint
from houses.nodes.property_nodes import PropertyNodes
from houses.services_provider import _request_services as _sp
from tests.helpers import make_services

LISTING_LOCATION = GeoPoint(51.48, -0.35)
PRECISE_LOCATION = GeoPoint(51.5, -0.37)
ASKING_PRICE = Money(amount="550000", currency="GBP")
TEST_ADDRESS = "31 Isambard Rd, SW1V 2QQ"


class _ExplodingPlanner:
    """A planner that errors for every route — the shape a dead upstream has."""

    @staticmethod
    async def walk_route(*_args, **_kwargs):
        raise RuntimeError("the walking planner is unreachable")

    @staticmethod
    async def drive_route(*_args, **_kwargs):
        raise RuntimeError("the driving planner is unreachable")


@pytest.fixture
def failing_prop():
    """A property priced against a planner that errors for every route."""
    token = _sp.set(make_services(route_planner=_ExplodingPlanner()))
    try:
        p = PropertyNodes("test_failure_surfaces")
        p.rightmove_price.push(ASKING_PRICE, "test")
        p.rightmove_address.push("31 Isambard Rd", "test")
        p.rightmove_bedrooms.push("3", "test")
        p.rightmove_location.push(LISTING_LOCATION, "rightmove")
        p.corrected_address.push(TEST_ADDRESS, "test")
        p.precise_location.push(PRECISE_LOCATION, "test")
        p.user_entered_address.push(TEST_ADDRESS, "test")
        yield p
    finally:
        _sp.reset(token)


@pytest.mark.asyncio
async def test_a_failed_commute_is_reported_as_failed(failing_prop):
    await flush_processor()
    summary = await failing_prop.to_json_summary()
    entries = {key: cd["commute"] for key, cd in summary["commutes"].items()}
    assert entries, "the summary must carry the commute entries"
    failed = {key: entry for key, entry in entries.items() if entry["status"] == "impossible"}
    assert failed, (
        "no commute reports a failure although every route errored — the failure "
        f"is hidden behind a plausible-looking entry: {entries}"
    )
    for key, entry in failed.items():
        assert entry["error"], f"{key}: the failure carries no reason to show the user"


@pytest.mark.asyncio
async def test_no_entry_papers_a_failure_over_as_an_empty_value(failing_prop):
    await flush_processor()
    summary = await failing_prop.to_json_summary()
    for key, cd in summary["commutes"].items():
        entry = cd["commute"]
        assert entry["status"] != "succeeded" or entry["value"] is not None, (
            f"{key}: a failed commute is reported as succeeded with no value — the "
            f"card shows a blank row and the cost vanishes from the total"
        )


@pytest.mark.asyncio
async def test_a_failed_commute_errors_the_household_total(failing_prop):
    await flush_processor()
    summary = await failing_prop.to_json_summary()
    failed = [key for key, cd in summary["commutes"].items() if cd["commute"]["status"] == "impossible"]
    assert failed, (
        "no commute reports a failure although every route errored — the total's "
        "honesty cannot be checked while the failure is hidden"
    )
    total = summary["group_monthly_cost"]
    assert total["status"] == "impossible", (
        f"{failed} could not be computed, yet the household total still reports a "
        f"number — the failure was silently omitted from the figures"
    )
    assert total["error"], "the total must carry the reason it could not be computed"
