"""Tests for DerivedNode refresh / staleness detection.

Restored from 52f3fb1^ and rewritten to target the DAG-based
``DerivedNode`` class instead of the old ``houses.model.resolver`` module.
"""

from __future__ import annotations

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode, flush_processor
from dag.persistence import latest_node_result
from dag.user_input_node import UserInputNode

# ── Helper subclasses ────────────────────────────────────────────────────────


class _DoubleNode(DerivedNode[int]):
    """Doubles the first dependency's value.  Tracks ``compute_count``."""

    def __init__(self, node_id: str, deps):
        super().__init__(node_id, int, deps)
        self.compute_count = 0

    def compute(self, *dep_attempts: Attempt) -> Attempt[int]:
        self.compute_count += 1
        val = dep_attempts[0]
        if val.succeeded:
            return Attempt.succeeded(val.value_or_none() * 2)
        return Attempt.impossible("dep failed")


class _SumNode(DerivedNode[int]):
    """Sums all dependency values.  Tracks ``compute_count``."""

    def __init__(self, node_id: str, deps):
        super().__init__(node_id, int, deps)
        self.compute_count = 0

    def compute(self, *dep_attempts: Attempt) -> Attempt[int]:
        self.compute_count += 1
        vals = [a.value_or_none() for a in dep_attempts]
        if all(a.succeeded for a in dep_attempts):
            return Attempt.succeeded(sum(vals))
        return Attempt.impossible("one or more deps failed")


# ── Tests ────────────────────────────────────────────────────────────────────


class TestDerivedNodeStaleness:
    """DerivedNode detects staleness and refreshes appropriately."""

    @pytest.mark.asyncio
    async def test_stale_after_dep_change(self):
        """After a dependency's value changes, the DerivedNode is stale
        and ``refresh()`` / ``flush_processor()`` re-computes it."""
        src = UserInputNode[int]("sadc_src", int)
        node = _DoubleNode("sadc_double", deps=(src,))

        src.push(5, "test")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 10
        assert node.compute_count == 1

        # Change dep again — node should recompute
        src.push(3, "test")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 6
        assert node.compute_count == 2

    @pytest.mark.asyncio
    async def test_not_stale_without_dep_change(self):
        """Without a dependency change, repeated ``attempt()`` calls
        return the cached result and ``compute`` is not re-executed."""
        src = UserInputNode[int]("nswdc_src", int)
        node = _DoubleNode("nswdc_double", deps=(src,))

        src.push(10, "test")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 20
        assert node.compute_count == 1

        # Second attempt — same dep, cached
        assert (await node.attempt()).value_or_none() == 20
        assert node.compute_count == 1

    @pytest.mark.asyncio
    async def test_staleness_reflected_in_to_json(self):
        """``DerivedNode.to_json()`` includes a ``stale`` key that
        reflects whether the node is out of date relative to its deps."""
        src = UserInputNode[int]("sritj_src", int)
        node = _DoubleNode("sritj_double", deps=(src,))

        src.push(4, "test")
        await flush_processor()
        await node.attempt()

        j = await node.to_json()
        assert j["stale"] is False

        # Change dep without flushing — node should report stale
        src.push(8, "test")
        j = await node.to_json()
        assert j["stale"] is True

    @pytest.mark.asyncio
    async def test_stale_status_cleared_by_flush(self):
        """After ``flush_processor()`` re-computes a stale node,
        ``to_json()`` reports ``stale=False``."""
        src = UserInputNode[int]("sscbf_src", int)
        node = _DoubleNode("sscbf_double", deps=(src,))

        src.push(4, "test")
        await flush_processor()
        await node.attempt()

        src.push(9, "test")  # node is now stale
        assert (await node.to_json())["stale"] is True

        await flush_processor()  # refresh it
        assert (await node.to_json())["stale"] is False
        assert (await node.attempt()).value_or_none() == 18

    @pytest.mark.asyncio
    async def test_persisted_stale_is_always_false(self):
        """When persisted (immediately after compute), ``stale`` is
        always ``False`` — persistence captures a fresh value."""
        src = UserInputNode[int]("psiaf_src", int)
        node = _DoubleNode("psiaf_double", deps=(src,))

        src.push(7, "test")
        await flush_processor()
        await node.attempt()

        loaded = latest_node_result("psiaf_double")
        assert loaded["stale"] is False
        assert loaded["value"] == 14

    @pytest.mark.asyncio
    async def test_flush_processor_multiple_stale(self):
        """``flush_processor()`` refreshes every node in the stale queue."""
        a = UserInputNode[int]("fpms_a", int)
        b = UserInputNode[int]("fpms_b", int)
        double_a = _DoubleNode("fpms_da", deps=(a,))
        double_b = _DoubleNode("fpms_db", deps=(b,))

        a.push(5, "t")
        b.push(6, "t")
        await flush_processor()
        assert (await double_a.attempt()).value_or_none() == 10
        assert (await double_b.attempt()).value_or_none() == 12

        a.push(7, "t")
        b.push(8, "t")
        await flush_processor()
        assert (await double_a.attempt()).value_or_none() == 14
        assert (await double_b.attempt()).value_or_none() == 16

    @pytest.mark.asyncio
    async def test_stale_any_dep_triggers_refresh(self):
        """Changing *any* one of multiple deps makes the node stale."""
        a = UserInputNode[int]("sadt_a", int)
        b = UserInputNode[int]("sadt_b", int)
        node = _SumNode("sadt_sum", deps=(a, b))

        a.push(2, "t")
        b.push(3, "t")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 5
        assert node.compute_count == 1

        # Change only a
        a.push(10, "t")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 13
        assert node.compute_count == 2

        # Change only b
        b.push(20, "t")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 30
        assert node.compute_count == 3

    @pytest.mark.asyncio
    async def test_refresh_noop_when_not_stale(self):
        """Calling ``refresh()`` on a node that is already fresh
        does not re-execute ``compute()``."""
        src = UserInputNode[int]("rnwns_src", int)
        node = _DoubleNode("rnwns_double", deps=(src,))

        src.push(10, "test")
        await flush_processor()
        assert node.compute_count == 1

        # Explicit refresh — should be a no-op
        await node.refresh()
        assert node.compute_count == 1
        assert (await node.attempt()).value_or_none() == 20

    @pytest.mark.asyncio
    async def test_transitive_staleness(self):
        """Staleness propagates through a chain of DerivedNodes."""
        src = UserInputNode[int]("ts_src", int)
        mid = _DoubleNode("ts_mid", deps=(src,))
        top = _DoubleNode("ts_top", deps=(mid,))

        src.push(3, "test")
        await flush_processor()
        assert (await top.attempt()).value_or_none() == 12  # 3*2*2
        assert top.compute_count == 1

        # Changing the leaf dep makes mid stale, which makes top stale
        src.push(5, "test")
        await flush_processor()
        assert (await mid.attempt()).value_or_none() == 10
        assert mid.compute_count == 2
        assert (await top.attempt()).value_or_none() == 20
        assert top.compute_count == 2
