"""Tests for the stale-node queue and flush_processor.

Every derived node that depends on a changed source should have a
succeeded attempt with the correct value after flush_processor returns.
"""

from __future__ import annotations

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode, flush_processor
from dag.node import Node
from dag.user_input_node import UserInputNode


class AddNode(DerivedNode[int]):
    """Adds together all incoming int deps."""

    def __init__(self, node_id: str, deps: tuple[Node, ...]):
        super().__init__(node_id, int, deps)

    def compute(self, *dep_attempts: Attempt) -> Attempt[int]:
        vals = [a.value_or_none() for a in dep_attempts if a.succeeded]
        return Attempt.succeeded(sum(vals))


class DoubleNode(DerivedNode[int]):
    """Doubles the first dep's value."""

    def __init__(self, node_id: str, deps: tuple[Node, ...]):
        super().__init__(node_id, int, deps)

    def compute(self, *dep_attempts: Attempt) -> Attempt[int]:
        val = dep_attempts[0].value_or_none()
        if val is None:
            return Attempt.impossible("no value")
        return Attempt.succeeded(val * 2)


@pytest.fixture(autouse=True)
def _clear_queue():
    # Queue is already isolated per-test via conftest._inject_test_scheduler
    yield


class TestBasicFlush:
    @pytest.mark.asyncio
    async def test_linear_chain(self):
        src = UserInputNode[int]("src2", int)
        dbl = DoubleNode("dbl", (src,))
        src.push(3, "test")
        await flush_processor()
        a = await dbl.attempt()
        assert a.succeeded, f"dbl failed: {a.error}"
        assert a.value_or_none() == 6

    @pytest.mark.asyncio
    async def test_two_link_chain(self):
        src = UserInputNode[int]("src3", int)
        dbl = DoubleNode("dbl2", (src,))
        add = AddNode("add", (dbl,))
        src.push(4, "test")
        await flush_processor()
        a = await add.attempt()
        assert a.succeeded, f"add failed: {a.error}"
        assert a.value_or_none() == 8

    @pytest.mark.asyncio
    async def test_two_independent_chains(self):
        sa = UserInputNode[int]("sa", int)
        sb = UserInputNode[int]("sb", int)
        da = DoubleNode("dbl_a", (sa,))
        db = DoubleNode("dbl_b", (sb,))
        sa.push(2, "test")
        sb.push(3, "test")
        await flush_processor()
        assert (await da.attempt()).value_or_none() == 4
        assert (await db.attempt()).value_or_none() == 6


class TestRequeueOnPendingDeps:
    @pytest.mark.asyncio
    async def test_diamond_deps(self):
        src = UserInputNode[int]("sd", int)
        db = DoubleNode("dbl_b", (src,))
        dc = DoubleNode("dbl_c", (src,))
        ad = AddNode("add_d", (db, dc))
        src.push(4, "test")
        await flush_processor()
        a = await ad.attempt()
        assert a.succeeded, f"add_d failed: {a.error}"
        assert a.value_or_none() == 16

    @pytest.mark.asyncio
    async def test_deep_chain(self):
        src = UserInputNode[int]("s_deep", int)
        b = DoubleNode("b_deep", (src,))
        c = DoubleNode("c_deep", (b,))
        d = AddNode("d_deep", (c,))
        src.push(2, "test")
        await flush_processor()
        a = await d.attempt()
        assert a.succeeded, f"d_deep failed: {a.error}"
        assert a.value_or_none() == 8

    @pytest.mark.asyncio
    async def test_source_update_propagates(self):
        src = UserInputNode[int]("s_upd", int)
        dbl = DoubleNode("dbl_upd", (src,))
        src.push(3, "test")
        await flush_processor()
        assert (await dbl.attempt()).value_or_none() == 6

        src.push(10, "test")
        await flush_processor()
        a2 = await dbl.attempt()
        assert a2.succeeded and a2.value_or_none() == 20

    @pytest.mark.asyncio
    async def test_no_infinite_loop(self):
        src = UserInputNode[int]("s_loop", int)
        prev: Node = src
        nodes: list[DoubleNode] = []
        for i in range(20):
            n = DoubleNode(f"loop_{i}", (prev,))
            nodes.append(n)
            prev = n
        src.push(1, "test")
        await flush_processor()
        a = await nodes[-1].attempt()
        assert a.succeeded, f"last node failed: {a.error}"
        assert a.value_or_none() == 1048576
