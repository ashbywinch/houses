from __future__ import annotations

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode, flush_processor
from dag.persistence import latest_node_result
from dag.user_input_node import UserInputNode


class TestDerivedNode:
    @pytest.mark.asyncio
    async def test_recomputes_on_dep_change(self):
        src = UserInputNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        src.push(2, "test")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 4

        src.push(3, "test")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 6

    @pytest.mark.asyncio
    async def test_initial_attempt_runs_compute(self):
        src = UserInputNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        a = await node.attempt()
        assert a.succeeded is False

        src.push(5, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.value_or_none() == 10

    @pytest.mark.asyncio
    async def test_caches_result_until_dep_changes(self):
        src = UserInputNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        src.push(10, "test")
        await flush_processor()
        first = await node.attempt()
        assert first.value_or_none() == 20

        second = await node.attempt()
        assert second.value_or_none() == 20
        assert node.compute_count == 1

    @pytest.mark.asyncio
    async def test_multiple_deps(self):
        a = UserInputNode[int]("a", int)
        b = UserInputNode[int]("b", int)
        node = _SumNode("sum", deps=(a, b))

        a.push(3, "t")
        b.push(4, "t")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 7

        a.push(10, "t")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 14

    @pytest.mark.asyncio
    async def test_changed_signal_fires_on_recompute(self):
        src = UserInputNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        received = []
        node.changed.connect(lambda: received.append("changed"))

        src.push(2, "test")
        await flush_processor()
        assert received == ["changed"]

        src.push(3, "test")
        await flush_processor()
        assert received == ["changed", "changed"]

    @pytest.mark.asyncio
    async def test_pending_when_dep_pending(self):
        src = UserInputNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        a = await node.attempt()
        assert a.pending is True

    @pytest.mark.asyncio
    async def test_impossible_when_dep_fails(self):
        """When a dep returns Attempt.impossible, the derived node should also be impossible."""
        # A node that returns impossible
        class _FailingNode(DerivedNode[int]):
            def __init__(self):
                super().__init__("fail_src", int, ())
                self._attempt = Attempt.impossible("always fails")

            def compute(self):
                return self._attempt

        src = _FailingNode()
        await flush_processor()

        doubler = _DoubleNode("double_fail_test", deps=(src,))
        await flush_processor()
        a = await doubler.attempt()
        assert a.impossible is True

    @pytest.mark.asyncio
    async def test_to_json(self):
        src = UserInputNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        src.push(4, "test")
        await flush_processor()
        j = await node.to_json()
        assert j["value"] == 8

    @pytest.mark.asyncio
    async def test_persists_after_compute(self):
        src = UserInputNode[int]("src_persist", int)
        node = _DoubleNode("double_persist", deps=(src,))

        src.push(7, "test")
        await flush_processor()
        await node.attempt()

        loaded = latest_node_result("double_persist")
        assert loaded is not None
        assert loaded["status"] == "succeeded"
        assert loaded["value"] == 14

    @pytest.mark.asyncio
    async def test_loads_from_db_on_init(self):
        src = UserInputNode[int]("src_reload", int)
        node1 = _DoubleNode("double_reload", deps=(src,))
        src.push(9, "test")
        await flush_processor()
        await node1.attempt()

        src2 = UserInputNode[int]("src_reload", int)
        node2 = _DoubleNode("double_reload", deps=(src2,))
        a = await node2.attempt()
        assert a.value_or_none() == 18

    @pytest.mark.asyncio
    async def test_staleness_timestamp_dep_push(self):
        src = UserInputNode[int]("src_stale", int)
        node = _DoubleNode("double_stale", deps=(src,))

        src.push(5, "test")
        await flush_processor()
        await node.attempt()
        assert node.compute_count == 1

        src.push(10, "test")
        await flush_processor()
        await node.attempt()
        assert node.compute_count == 2

    @pytest.mark.asyncio
    async def test_async_compute(self):
        src = UserInputNode[int]("src_async", int)
        node = _AsyncDoubleNode("double_async", deps=(src,))

        src.push(3, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.value_or_none() == 6

    @pytest.mark.asyncio
    async def test_dep_timestamps_not_returned_by_latest(self):
        src = UserInputNode[int]("src_dep_ts", int)
        node = _DoubleNode("double_dep_ts", deps=(src,))

        src.push(42, "test")
        await flush_processor()
        await node.attempt()

        loaded = latest_node_result("double_dep_ts")
        assert loaded is not None
        assert loaded["status"] == "succeeded"
        assert loaded["value"] == 84


class _DoubleNode(DerivedNode[int]):
    def __init__(self, node_id: str, deps):
        super().__init__(node_id, int, deps)
        self.compute_count = 0

    def compute(self, *dep_attempts) -> Attempt[int]:
        self.compute_count += 1
        val = dep_attempts[0]
        if val.succeeded:
            return Attempt.succeeded(val.value_or_none() * 2)
        return Attempt.impossible("dep failed")


class _SumNode(DerivedNode[int]):
    def __init__(self, node_id: str, deps):
        super().__init__(node_id, int, deps)
        self.compute_count = 0

    def compute(self, *dep_attempts) -> Attempt[int]:
        self.compute_count += 1
        vals = [a.value_or_none() for a in dep_attempts]
        if all(a.succeeded for a in dep_attempts):
            return Attempt.succeeded(sum(vals))
        return Attempt.impossible("one or more deps failed")


class _AsyncDoubleNode(DerivedNode[int]):
    def __init__(self, node_id: str, deps):
        super().__init__(node_id, int, deps)

    async def compute(self, *dep_attempts) -> Attempt[int]:
        val = dep_attempts[0]
        if val.succeeded:
            return Attempt.succeeded(val.value_or_none() * 2)
        return Attempt.impossible("dep failed")
