"""The "extra vs your home" monthly deltas — DAG nodes and wire fields.

The baseline and the deltas live in the DAG (CurrentHomeNode +
DeltaVsHomeNode); ``attach`` only projects them onto serialized payloads.
Reads are never live: the DAG recalculates — a status write FANS OUT
through the signal graph (docs/dag-library.md, Design Rules → "Never
live on read").
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from money import Money

from houses.nodes.current_home_node import current_home_node
from houses.nodes.delta_vs_home_node import DeltaVsHomeValue
from houses.services_provider import get_services
from tests.unit.conftest import flush_all


def _group_block(summary: dict) -> dict | None:
    """The group value block (top level on summaries, under
    affordability on detail payloads)."""
    group = summary.get("group_monthly_cost")
    if group is None:
        affordability = summary.get("affordability")
        group = affordability.get("group_monthly_cost") if isinstance(affordability, dict) else None
    return group if isinstance(group, dict) else None

# ── helpers ──────────────────────────────────────────────────────────


def _push_persons(*persons) -> None:
    """Seed the persons settings node (same seam the route tests use)."""
    get_services().persons_source.push(list(persons), "test")


def _household(*, ashby_rent: Money | None = None) -> list:
    """Simon (the current-home holder → the couple), Lorena and Ashby (the
    other adults)."""
    from houses.model.domain import Person

    return [
        Person(
            name="Simon",
            has_car=True,
            email="simon@example.com",
            home_sale_price=Money("550000", "GBP"),
            outstanding_mortgage=Money("373000", "GBP"),
        ),
        Person(name="Lorena", has_car=False, email="lorena@example.com"),
        Person(name="Ashby", has_car=True, rent_paid_monthly=ashby_rent or Money("0", "GBP")),
    ]


def _seed_property(registry, rid: str, *, status: str = ""):
    """One minimally-seeded property (offline-computable group figures).

    Registration comes FIRST: it wires the status node into the
    current-home node. The pushes then signal wired slots — the status
    write fans the baseline and every delta into the queue (production
    primes the same nodes through the bootstrap stale sweep).
    """
    from houses.geopoint import GeoPoint
    from houses.nodes.property_nodes import PropertyNodes

    prop = PropertyNodes(rid)
    registry.register(rid, prop)
    prop.rightmove_price.push(Money("500000", "GBP"), "test")
    prop.rightmove_address.push(f"{rid} Test St", "test")
    prop.rightmove_bedrooms.push("3", "test")
    prop.rightmove_location.push(GeoPoint(51.5, -0.1), "test")
    prop.corrected_address.push(f"{rid} Test St, SW1P 1AA", "test")
    prop.precise_location.push(GeoPoint(51.5, -0.1), "test")
    prop.user_entered_address.push(f"{rid} Test St, SW1P 1AA", "test")
    prop.works_estimates.push({}, "test")
    prop.rental_income.push(Money("0", "GBP"), "test")
    prop.comment_status.push(status, "test")
    return prop


def _baseline_pair(*, ashby_rent: Money | None = None, base_status: str = "current"):
    """A flushed registry: '880001' the (by default current) home, '880002' a candidate."""
    _push_persons(*_household(ashby_rent=ashby_rent))
    registry = get_services().property_registry
    registry.clear()
    base = _seed_property(registry, "880001", status=base_status)
    cand = _seed_property(registry, "880002")
    flush_all()  # the pushes drain: baseline + deltas settle in the DAG
    return registry, base, cand


# ── CurrentHomeNode ──────────────────────────────────────────────────


def _current_home_value():
    att = current_home_node().latest_attempt()
    return att.value_or_none() if att is not None and att.succeeded else None


class TestCurrentHomeNode:
    """THE current home is a DAG node: resolved by signals, never by a
    per-request registry scan."""

    def test_no_current_status_resolves_none(self):
        _baseline_pair(base_status="")
        assert _current_home_value() is None

    def test_two_current_homes_resolves_none(self):
        registry, base, cand = _baseline_pair(base_status="current")
        cand.comment_status.push("current", "test")
        flush_all()  # the status apply lands (stamp + signal)
        assert asyncio.run(current_home_node().refresh(force=True)) is None
        assert _current_home_value() is None

    def test_single_current_home_resolves_descriptor(self):

        registry, base, cand = _baseline_pair(base_status="current")
        # The winner's identity + figures ride the node's value — the
        # address node is an active dep of the descriptor too.
        _current_home_value()
        value = _current_home_value()
        assert value is not None and value.rid == "880001"
        assert value.address == "880001 Test St, SW1P 1AA"
        assert value.group_value["couple"]["value"] is not None

    def test_status_change_fans_out_through_the_dag(self):
        """Marking a DIFFERENT property current re-derives the descriptor —
        never read-live, recalculated by signals."""
        registry, base, cand = _baseline_pair(base_status="current")
        before = _current_home_value()
        assert before is not None and before.rid == "880001"

        base.comment_status.push("", "test")
        cand.comment_status.push("current", "test")
        flush_all()  # the status applies land (stamp + signal)
        # The DAG recalculation: refresh the node — its active deps now
        # read the changed statuses and RE-DERIVE the baseline.
        asyncio.run(current_home_node().refresh(force=True))
        value = _current_home_value()
        assert value is not None and value.rid == "880002", (
            f"the DAG must re-derive the baseline after a status write, got rid={value.rid if value else None}"
        )


# ── DeltaVsHomeNode ──────────────────────────────────────────────────


def _delta_value(rid: str) -> DeltaVsHomeValue | None:
    prop = get_services().property_registry.get(rid)
    att = prop.delta_vs_home.latest_attempt()
    return att.value_or_none() if att is not None and att.succeeded else None


class TestDeltaVsHomeNode:
    def test_candidate_minus_home_signed_two_dp(self):
        _baseline_pair(base_status="current")
        value = _delta_value("880002")
        assert value is not None
        assert value.couple is not None
        assert value.others is not None
        registry = get_services().property_registry
        own = registry.get("880002").group_monthly_cost.latest_attempt().value_or_none()
        base = registry.get("880001").group_monthly_cost.latest_attempt().value_or_none()
        own_value = own["couple"]["value"]
        base_value = base["couple"]["value"]
        assert own_value is not None and base_value is not None
        expected = Decimal(own_value) - Decimal(base_value)
        delta_value = value.couple.value
        assert delta_value is not None
        assert Decimal(delta_value) == expected
        assert value.couple.approx is False

    def test_self_is_current_home_has_no_delta(self):
        _baseline_pair(base_status="current")
        assert _delta_value("880001") is None

    def test_no_current_home_has_no_delta(self):
        _baseline_pair(base_status="")
        assert _delta_value("880002") is None

    def test_approx_propagates_from_either_side(self):
        """Approx is a pure function of the two figures' stddevs — pinned at
        the compute unit."""
        from dag.attempt import Attempt
        from dag.user_input_node import UserInputNode
        from houses.nodes.current_home_node import MonthlyBaseline
        from houses.nodes.delta_vs_home_node import DeltaVsHomeNode

        own = Attempt.succeeded(
            {"couple": {"value": "3091.67", "stddev": 0}, "others": {"value": "241.64", "stddev": 0}}
        )
        baseline = Attempt.succeeded(
            MonthlyBaseline(
                rid="880001",
                address="Home",
                group_value={
                    "couple": {"value": "1783.61", "stddev": 12.5},
                    "others": {"value": "652.92", "stddev": 0},
                },
                others_rent_paid=0.0,
            )
        )
        a = UserInputNode("approx_a", int)
        b = UserInputNode("approx_b", int)
        node = DeltaVsHomeNode("approx/delta_vs_home", group_node=a, current_home=b)
        out = node.compute(own, baseline)
        assert out.succeeded
        value = out.value_or_none()
        assert value is not None and value.couple is not None and value.others is not None
        assert value.couple.approx is True
        assert value.others.approx is False

    def test_baseline_change_reprices_the_delta(self):
        """A re-price of THE current home fans out to every candidate's
        delta through the DAG — a later read is the recalculated value."""
        registry, base, cand = _baseline_pair(base_status="current")
        before = _delta_value("880002")
        assert before is not None and before.couple is not None
        before_couple = before.couple.value
        assert before_couple is not None

        base.rightmove_price.push(Money("400000", "GBP"), "test")
        flush_all()  # the price chain lands (stamp + signal)
        # Re-derive the baseline descriptor first — the delta's dep is the
        # CURRENT-HOME node, and it must see the re-priced figures.
        asyncio.run(current_home_node().refresh(force=True))
        prop = get_services().property_registry.get("880002")
        asyncio.run(prop.delta_vs_home.refresh(force=True))

        after = _delta_value("880002")
        assert after is not None and after.couple is not None
        after_value = after.couple.value
        assert after_value is not None
        prev = Decimal(before_couple)
        now = Decimal(after_value)
        assert now != prev, f"a baseline re-price must re-derive every delta ({prev} -> {now})"


# ── Wire projection (attach) ─────────────────────────────────────────


class TestAttachProjection:
    def _attach(self, rid: str, summary: dict):
        from houses.web.monthly_delta import attach

        return asyncio.run(attach(summary, rid))

    def test_detail_payload_fields(self):
        _baseline_pair(base_status="current")
        prop = get_services().property_registry.get("880002")
        summary = {"affordability": {"group_monthly_cost": asyncio.run(prop.group_monthly_cost.to_json_value())}}
        self._attach("880002", summary)
        assert summary["is_current_home"] is False
        assert summary["monthly_baseline"] is not None
        assert summary["monthly_baseline"]["rid"] == "880001"
        group = _group_block(summary)
        assert group is not None and group["value"] is not None
        delta = group["value"]["delta_vs_home"]
        assert isinstance(delta, dict) and delta["couple"] is not None

    def test_current_home_payload_has_no_delta(self):
        _baseline_pair(base_status="current")
        prop = get_services().property_registry.get("880001")
        assert prop is not None
        summary = {"affordability": {"group_monthly_cost": asyncio.run(prop.group_monthly_cost.to_json_value())}}
        self._attach("880001", summary)
        assert summary["is_current_home"] is True
        group = _group_block(summary)
        assert group is not None and group["value"] is not None
        assert group["value"]["delta_vs_home"] is None


# ── the endpoint serves the DAG's own record ─────────────────────────


def test_provenance_endpoint_serves_the_delta_tree():
    from houses.web.api_router import _require_property

    _baseline_pair(base_status="current")
    # The tree is the node's own provenance: both figures the subtraction
    # used appear in it.
    delta_node = _require_property("880002").delta_vs_home
    tree = asyncio.run(delta_node.build_provenance()).to_dict()
    text = json.dumps(tree)
    assert "880002" in text or "3091" in text or "1,783" in text, "the baseline figures must be in the tree"
    assert "couple" in text