"""Force-regeneration: recompute nodes that are NOT stale but should be
(code changed under them), plus the node-pattern matcher.

The refresh path skips non-stale nodes; after a code change the persisted
results are wrong but timestamps say fresh. ``refresh(force=True)`` and
``force_regenerate`` bypass the staleness check.
"""

from __future__ import annotations

from typing import override

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.regenerate import force_regenerate, nodes_matching, pattern_regex
from dag.scheduler import AsyncQueueScheduler, flush_processor, reset_scheduler, set_scheduler
from dag.user_input_node import UserInputNode


class _Counter(DerivedNode[int]):
    """Recomputes only when refreshed; counts invocations."""

    calls = 0

    def __init__(self, node_id: str, source):
        super().__init__(node_id, int, (source,))

    @override
    def compute(self, *dep_attempts: Attempt) -> Attempt[int]:
        _Counter.calls += 1
        return Attempt.succeeded(dep_attempts[0].value_or(0) + _Counter.calls)


@pytest.fixture(autouse=True)
def _isolated_scheduler():
    set_scheduler(AsyncQueueScheduler(respect_time=False))
    yield
    reset_scheduler()


class TestPatternMatcher:
    def test_exact_id_matches_only_itself(self):
        rx = pattern_regex("123/council_tax")
        assert rx.match("123/council_tax")
        assert not rx.match("456/council_tax")

    def test_wildcard_matches_any_prefix(self):
        rx = pattern_regex("*/council_tax")
        assert rx.match("123/council_tax")
        assert rx.match("456/council_tax")
        assert not rx.match("council_tax")
        assert not rx.match("123/other")

    def test_bare_star_matches_everything(self):
        rx = pattern_regex("*")
        assert rx.match("anything/at/all")

    def test_pattern_is_anchored(self):
        rx = pattern_regex("*council")
        assert rx.match("123/council")
        assert not rx.match("123/council_tax")

    def test_nodes_matching_collects_by_id(self):
        a = UserInputNode[int]("rg_a", int)
        b = UserInputNode[int]("rg_b", int)
        found = nodes_matching(["*b"], [a, b])
        assert [n._id for n in found] == ["rg_b"]


class TestForceRegenerate:
    @pytest.mark.asyncio
    async def test_plain_refresh_skips_non_stale_node(self):
        src = UserInputNode[int]("rg_c", int)
        node = _Counter("rg_d", src)
        src.push(10, "user")
        await flush_processor()
        _Counter.calls = 0
        await node.refresh()
        assert _Counter.calls == 0, "non-stale node must not recompute"

    @pytest.mark.asyncio
    async def test_force_refresh_recomputes_non_stale_node(self):
        src = UserInputNode[int]("rg_e", int)
        node = _Counter("rg_f", src)
        src.push(10, "user")
        await flush_processor()
        _Counter.calls = 0
        await node.refresh(force=True)
        assert _Counter.calls == 1, "force must bypass the staleness check"
        assert node.latest_attempt().value == 11

    @pytest.mark.asyncio
    async def test_force_regenerate_skips_input_nodes(self):
        src = UserInputNode[int]("rg_g", int)
        node = _Counter("rg_h", src)
        src.push(10, "user")
        await flush_processor()
        regenerated, skipped = await force_regenerate([src, node])
        assert [s["node"] for s in skipped] == ["rg_g"]
        assert [r["node"] for r in regenerated] == ["rg_h"]
        assert regenerated[0]["status"] == "succeeded"
