"""The "extra vs your home" monthly deltas — wire fields and computation.

The backend attaches, at the serialization boundary (never inside the DAG
node), three fields to every property summary and detail payload:

- ``is_current_home`` — comment_status == 'current' (case/space-insensitive)
- ``monthly_baseline`` — THE single current home's identity + figures, or null
- ``group_monthly_cost.value.delta_vs_home`` — per-group candidate − baseline

Zero or several current homes (or an uncomputable baseline figure) →
``monthly_baseline`` is null EVERYWHERE: cards fall back to today's totals.
Never zeros-as-meaning.  The what-if response's hypothetical ``group`` gains
the same ``delta_vs_home``, computed against the REAL baseline.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from decimal import Decimal
from typing import cast

import pytest
from fastapi import WebSocket
from money import Money

from dag.scheduler import flush_processor
from houses.services_provider import get_services

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
    """One minimally-seeded property (offline-computable group figures)."""
    from houses.geopoint import GeoPoint
    from houses.nodes.property_nodes import PropertyNodes

    prop = PropertyNodes(rid)
    prop.rightmove_price.push(Money("500000", "GBP"), "test")
    prop.rightmove_address.push(f"{rid} Test St", "test")
    prop.rightmove_bedrooms.push("3", "test")
    prop.rightmove_location.push(GeoPoint(51.5, -0.1), "test")
    prop.corrected_address.push(f"{rid} Test St, SW1V 2QQ", "test")
    prop.precise_location.push(GeoPoint(51.5, -0.1), "test")
    prop.user_entered_address.push(f"{rid} Test St, SW1V 2QQ", "test")
    prop.works_estimates.push({}, "test")
    prop.rental_income.push(Money("0", "GBP"), "test")
    prop.comment_status.push(status, "test")
    registry.register(rid, prop)
    return prop


def _baseline_pair(*, ashby_rent: Money | None = None, base_status: str = "current"):
    """A flushed registry: '880001' the (by default current) home, '880002' a candidate."""
    _push_persons(*_household(ashby_rent=ashby_rent))
    registry = get_services().property_registry
    registry.clear()
    base = _seed_property(registry, "880001", status=base_status)
    cand = _seed_property(registry, "880002")
    return registry, base, cand


async def _until(condition, timeout: float = 2.0, message: str = "") -> None:
    """Wait for *condition* inside the running loop (broadcaster pushes are
    asynchronous to the queue put)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError(message or "condition not met in time")
        await asyncio.sleep(0.01)


# ── delta computation (pure) ─────────────────────────────────────────


def _baseline(group_value: dict):
    from houses.web.monthly_delta import MonthlyBaseline

    return MonthlyBaseline(
        rid="880001", address="31 Isambard Road", group_value=group_value, others_rent_paid=600.0
    )


class TestGroupDelta:
    """delta_vs_home — candidate − baseline, per group."""

    CANDIDATE = {"couple": {"value": "3091.67", "stddev": 0.0}, "others": {"value": "241.64", "stddev": 0.0}}
    BASELINE = {"couple": {"value": "1783.61", "stddev": 0.0}, "others": {"value": "652.92", "stddev": 0.0}}

    def test_both_sides_succeed_signed_two_dp(self):
        from houses.web.monthly_delta import delta_vs_home

        delta = delta_vs_home(self.CANDIDATE, _baseline(self.BASELINE))
        assert delta == {
            "couple": {"value": "+1308.06", "approx": False},
            "others": {"value": "-411.28", "approx": False},
        }

    def test_zero_delta_keeps_explicit_sign_and_two_dp(self):
        from houses.web.monthly_delta import delta_vs_home

        delta = delta_vs_home(self.CANDIDATE, _baseline(self.CANDIDATE))
        assert delta["couple"] == {"value": "+0.00", "approx": False}

    def test_approx_from_candidate_stddev(self):
        from houses.web.monthly_delta import delta_vs_home

        candidate = {"couple": {"value": "3091.67", "stddev": 12.5}, "others": {"value": "241.64", "stddev": 0.0}}
        delta = delta_vs_home(candidate, _baseline(self.BASELINE))
        assert delta["couple"]["approx"] is True
        assert delta["others"]["approx"] is False

    def test_approx_from_baseline_stddev(self):
        from houses.web.monthly_delta import delta_vs_home

        baseline = {"couple": {"value": "1783.61", "stddev": 3.0}, "others": {"value": "652.92", "stddev": 0.0}}
        delta = delta_vs_home(self.CANDIDATE, _baseline(baseline))
        assert delta["couple"]["approx"] is True
        assert delta["others"]["approx"] is False

    def test_candidate_group_uncomputable_gives_null_group(self):
        from houses.web.monthly_delta import delta_vs_home

        candidate = {"couple": None, "others": {"value": "241.64", "stddev": 0.0}}
        delta = delta_vs_home(candidate, _baseline(self.BASELINE))
        assert delta["couple"] is None
        assert delta["others"] == {"value": "-411.28", "approx": False}

    def test_baseline_group_uncomputable_gives_null_group(self):
        from houses.web.monthly_delta import delta_vs_home

        baseline = {"couple": {"value": "1783.61", "stddev": 0.0}, "others": None}
        delta = delta_vs_home(self.CANDIDATE, _baseline(baseline))
        assert delta["couple"] == {"value": "+1308.06", "approx": False}
        assert delta["others"] is None


class TestMonthlyBaselineWire:
    """MonthlyBaseline.to_wire — the contract's monthly_baseline shape."""

    def test_wire_shape(self):
        baseline = _baseline(
            {
                "couple": {"value": "1783.61", "stddev": 0.0},
                "others": {"value": "652.92", "stddev": 7.0},
            }
        )
        assert baseline.to_wire() == {
            "rid": "880001",
            "address": "31 Isambard Road",
            "couple": {"value": "1783.61", "approx": False},
            "others": {"value": "652.92", "approx": True},
            "others_rent_paid": 600.0,
        }

    def test_wire_others_null_when_uncomputable(self):
        baseline = _baseline(
            {"couple": {"value": "1783.61", "stddev": 0.0}, "others": None}
        )
        wire = baseline.to_wire()
        assert wire["couple"] == {"value": "1783.61", "approx": False}
        assert wire["others"] is None
        assert wire["others_rent_paid"] == 600.0


# ── baseline resolution ──────────────────────────────────────────────


class TestBaselineResolution:
    @pytest.mark.asyncio
    async def test_single_current_home_resolves(self):
        from houses.web.monthly_delta import resolve_baseline

        registry, base, _cand = _baseline_pair(ashby_rent=Money("600", "GBP"))
        await flush_processor()

        baseline = resolve_baseline(registry)
        assert baseline is not None
        assert baseline.rid == "880001"
        wire = baseline.to_wire()
        expected_address = str(base.best_address.latest_attempt().value_or_none())
        assert wire["address"] == expected_address
        own_group = base.group_monthly_cost.latest_attempt().value_or_none()
        assert own_group is not None
        assert wire["couple"]["value"] == own_group["couple"]["value"]
        assert wire["others_rent_paid"] == 600.0

    @pytest.mark.asyncio
    async def test_status_match_is_case_and_space_insensitive(self):
        from houses.web.monthly_delta import resolve_baseline

        _push_persons(*_household())
        registry = get_services().property_registry
        registry.clear()
        _seed_property(registry, "880001", status="  CURRENT ")
        await flush_processor()

        baseline = resolve_baseline(registry)
        assert baseline is not None
        assert baseline.rid == "880001"

    @pytest.mark.asyncio
    async def test_zero_current_homes_returns_none(self):
        from houses.web.monthly_delta import resolve_baseline

        registry, _base, _cand = _baseline_pair(base_status="")
        await flush_processor()

        assert resolve_baseline(registry) is None

    @pytest.mark.asyncio
    async def test_multiple_current_homes_returns_none(self):
        from houses.web.monthly_delta import resolve_baseline

        _push_persons(*_household())
        registry = get_services().property_registry
        registry.clear()
        _seed_property(registry, "880001", status="current")
        _seed_property(registry, "880002", status="Current")
        await flush_processor()

        assert resolve_baseline(registry) is None

    @pytest.mark.asyncio
    async def test_current_home_without_computed_group_figure_returns_none(self):
        from houses.web.monthly_delta import resolve_baseline

        registry, _base, _cand = _baseline_pair()
        # Deliberately NO flush — the group figure is still pending, so the
        # current home has no computed couple value: baseline inactive.

        assert resolve_baseline(registry) is None


# ── attachment to serialized payloads ────────────────────────────────


class TestAttach:
    @pytest.mark.asyncio
    async def test_baseline_summary_is_current_with_null_delta(self):
        from houses.web.monthly_delta import attach

        registry, base, _cand = _baseline_pair(ashby_rent=Money("600", "GBP"))
        await flush_processor()

        summary = await base.to_json_summary()
        await attach(summary, "880001", registry)

        assert summary["is_current_home"] is True
        assert summary["monthly_baseline"]["rid"] == "880001"
        assert summary["monthly_baseline"]["others_rent_paid"] == 600.0
        assert summary["group_monthly_cost"]["value"]["delta_vs_home"] is None

    @pytest.mark.asyncio
    async def test_candidate_summary_gets_delta_vs_baseline(self):
        from houses.web.monthly_delta import attach

        registry, _base, cand = _baseline_pair()
        await flush_processor()

        summary = await cand.to_json_summary()
        await attach(summary, "880002", registry)

        assert summary["is_current_home"] is False
        assert summary["monthly_baseline"]["rid"] == "880001"

        value = summary["group_monthly_cost"]["value"]
        delta = value["delta_vs_home"]
        assert re.fullmatch(r"[+-]\d+\.\d{2}", delta["couple"]["value"]), delta["couple"]
        assert re.fullmatch(r"[+-]\d+\.\d{2}", delta["others"]["value"]), delta["others"]
        expected_couple = Decimal(value["couple"]["value"]) - Decimal(
            summary["monthly_baseline"]["couple"]["value"]
        )
        assert Decimal(delta["couple"]["value"]) == expected_couple
        assert delta["couple"]["approx"] is (
            value["couple"]["stddev"] > 0 or summary["monthly_baseline"]["couple"]["approx"]
        )

    @pytest.mark.asyncio
    async def test_attach_does_not_mutate_the_node_value(self):
        from houses.web.monthly_delta import attach

        registry, _base, cand = _baseline_pair()
        await flush_processor()

        summary = await cand.to_json_summary()
        await attach(summary, "880002", registry)
        assert "delta_vs_home" in summary["group_monthly_cost"]["value"]

        fresh = await cand.group_monthly_cost.to_json_value()
        assert "delta_vs_home" not in fresh["value"], "attach leaked into the DAG node value"

    @pytest.mark.asyncio
    async def test_no_baseline_null_everywhere(self):
        from houses.web.monthly_delta import attach

        registry, base, cand = _baseline_pair(base_status="")
        await flush_processor()

        for rid, prop in (("880001", base), ("880002", cand)):
            summary = await prop.to_json_summary()
            await attach(summary, rid, registry)
            assert summary["is_current_home"] is False, rid
            assert summary["monthly_baseline"] is None, rid
            assert summary["group_monthly_cost"]["value"]["delta_vs_home"] is None, rid

    @pytest.mark.asyncio
    async def test_two_current_homes_null_baseline_but_current_flag_kept(self):
        from houses.web.monthly_delta import attach

        _push_persons(*_household())
        registry = get_services().property_registry
        registry.clear()
        base = _seed_property(registry, "880001", status="current")
        cand = _seed_property(registry, "880002", status=" Current ")
        await flush_processor()

        for rid, prop in (("880001", base), ("880002", cand)):
            summary = await prop.to_json_summary()
            await attach(summary, rid, registry)
            assert summary["is_current_home"] is True, rid
            assert summary["monthly_baseline"] is None, rid
            assert summary["group_monthly_cost"]["value"]["delta_vs_home"] is None, rid


# ── broadcaster freshness ────────────────────────────────────────────


class _FakeWS:
    """Minimal stand-in: never handshakes, only records pushed JSON."""

    def __init__(self):
        self.messages: list[dict] = []

    async def send_text(self, msg: str) -> None:
        self.messages.append(json.loads(msg))


class TestBroadcasterBaselineFreshness:
    @pytest.mark.asyncio
    async def test_baseline_update_pushes_fresh_summaries_for_every_rid(self):
        import houses.web.broadcaster as bcast

        bcast._reset()
        registry, _base, _cand = _baseline_pair()
        _seed_property(registry, "880003")
        await flush_processor()

        ws = _FakeWS()
        bcast._websocket_clients.add(cast(WebSocket, ws))
        await bcast._broadcast_queue.put("880001")
        task = asyncio.create_task(bcast._broadcaster())
        try:
            await _until(
                lambda: len(ws.messages) == 3,
                message=f"expected 3 summary pushes, got {[m['rid'] for m in ws.messages]}",
            )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert [m["type"] for m in ws.messages] == ["property_updated"] * 3
        assert ws.messages[0]["rid"] == "880001"
        assert {m["rid"] for m in ws.messages[1:]} == {"880002", "880003"}
        # The baseline card keeps totals (null delta); every other card's
        # summary was REBUILT fresh with its delta vs the baseline.
        assert ws.messages[0]["data"]["group_monthly_cost"]["value"]["delta_vs_home"] is None
        for msg in ws.messages[1:]:
            delta = msg["data"]["group_monthly_cost"]["value"]["delta_vs_home"]
            assert delta is not None and delta["couple"]["value"].startswith(("+", "-"))
            assert msg["data"]["monthly_baseline"]["rid"] == "880001"

    @pytest.mark.asyncio
    async def test_non_baseline_update_pushes_only_that_summary(self):
        import houses.web.broadcaster as bcast

        bcast._reset()
        registry, _base, _cand = _baseline_pair()
        await flush_processor()

        ws = _FakeWS()
        bcast._websocket_clients.add(cast(WebSocket, ws))
        await bcast._broadcast_queue.put("880002")
        task = asyncio.create_task(bcast._broadcaster())
        try:
            await _until(lambda: len(ws.messages) == 1, message="candidate push never arrived")
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert [m["rid"] for m in ws.messages] == ["880002"]

    @pytest.mark.asyncio
    async def test_notify_node_refreshed_pushes_property_summaries(self):
        """THE DAG contract: any node refresh notifies the frontend. A
        property node refresh queues that rid; the debounced flush pushes
        one fresh summary per changed property."""
        from types import SimpleNamespace

        import houses.web.broadcaster as bcast

        bcast._reset()
        registry, _base, _cand = _baseline_pair()
        await flush_processor()

        ws = _FakeWS()
        bcast._websocket_clients.add(cast(WebSocket, ws))
        task = asyncio.create_task(bcast._broadcaster())

        # Two property nodes refresh (e.g. a what-if apply touched the
        # persons input): both rids are notified.
        bcast.notify_node_refreshed(SimpleNamespace(_id="880002/group_monthly_cost"))
        bcast.notify_node_refreshed(SimpleNamespace(_id="880001/works_estimates"))
        try:
            await _until(
                lambda: {m["rid"] for m in ws.messages} >= {"880001", "880002"},
                message="both notified rids must be pushed",
            )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # The sweep may add extra pushes (Keep-scenario side effects);
        # both notified rids must have arrived.
        rids = {m["rid"] for m in ws.messages}
        assert {"880001", "880002"} <= rids
        assert all(m["type"] == "property_updated" for m in ws.messages)

    @pytest.mark.asyncio
    async def test_notify_node_refreshed_ignores_non_property_nodes(self):
        from types import SimpleNamespace

        import houses.web.broadcaster as bcast

        bcast._reset()
        registry, _base, _cand = _baseline_pair()
        await flush_processor()

        ws = _FakeWS()
        bcast._websocket_clients.add(cast(WebSocket, ws))
        bcast.notify_node_refreshed(SimpleNamespace(_id="persons"))
        bcast.notify_node_refreshed(SimpleNamespace(_id="settings/mortgage_rate"))
        await asyncio.sleep(0.05)

        assert ws.messages == [], "non-property nodes must not trigger property broadcasts"
