"""Tests for the generic IfThenElseNode."""

from __future__ import annotations

import pytest

from dag.attempt import Attempt
from dag.derived_node import AsyncQueueScheduler, flush_processor, set_scheduler
from dag.if_then_else import IfThenElseNode
from dag.user_input_node import UserInputNode


@pytest.fixture(autouse=True)
def _isolated_scheduler():
    set_scheduler(AsyncQueueScheduler(respect_time=False))
    yield
    set_scheduler(None)


class TestIfThenElseNode:
    """IfThenElseNode — condition-driven branch activation."""

    @pytest.mark.asyncio
    async def test_condition_true_passes_then_branch(self):
        """When condition_fn returns True, then_branch result is passed through."""
        cond = UserInputNode[bool]("cond", bool)
        then_src = UserInputNode[str]("then", str)
        else_src = UserInputNode[str]("else", str)

        node = IfThenElseNode(
            "ite1",
            str,
            condition_sources=(cond,),
            condition_fn=lambda a: a.value_or(False),
            then_branch=then_src,
            else_branch=else_src,
        )

        cond.push(True, "user")
        then_src.push("then_value", "user")
        else_src.push("else_value", "user")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "then_value"

    @pytest.mark.asyncio
    async def test_condition_false_returns_else_branch(self):
        """When condition_fn returns False, else_branch result is passed through."""
        cond = UserInputNode[bool]("cond2", bool)
        then_src = UserInputNode[str]("then2", str)
        else_src = UserInputNode[str]("else2", str)

        node = IfThenElseNode(
            "ite2",
            str,
            condition_sources=(cond,),
            condition_fn=lambda a: a.value_or(False),
            then_branch=then_src,
            else_branch=else_src,
        )

        cond.push(False, "user")
        then_src.push("then_value", "user")
        else_src.push("else_value", "user")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "else_value"

    @pytest.mark.asyncio
    async def test_condition_pending_blocks(self):
        """When condition_source is pending, node stays pending."""
        cond = UserInputNode[bool]("cond3", bool)
        then_src = UserInputNode[str]("then3", str)

        node = IfThenElseNode(
            "ite3",
            str,
            condition_sources=(cond,),
            condition_fn=lambda a: a.value_or(False),
            then_branch=then_src,
        )

        then_src.push("then_value", "user")
        # cond never pushed → pending

        await flush_processor()

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_then_branch_pending_when_true_blocks(self):
        """When condition is True but then_branch is pending, node stays pending."""
        cond = UserInputNode[bool]("cond4", bool)
        then_src = UserInputNode[str]("then4", str)

        node = IfThenElseNode(
            "ite4",
            str,
            condition_sources=(cond,),
            condition_fn=lambda a: a.value_or(False),
            then_branch=then_src,
        )

        cond.push(True, "user")
        # then_src never pushed → pending

        await flush_processor()

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_condition_false_no_else_returns_none(self):
        """When condition is False and no else_branch, returns None."""
        cond = UserInputNode[bool]("cond5", bool)
        then_src = UserInputNode[str]("then5", str)

        node = IfThenElseNode(
            "ite5",
            str,
            condition_sources=(cond,),
            condition_fn=lambda a: a.value_or(False),
            then_branch=then_src,
        )

        cond.push(False, "user")
        then_src.push("then_value", "user")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() is None

    @pytest.mark.asyncio
    async def test_multiple_condition_sources(self):
        """AND-like condition over two sources."""
        a_src = UserInputNode[bool]("a", bool)
        b_src = UserInputNode[bool]("b", bool)
        then_src = UserInputNode[str]("then6", str)

        node = IfThenElseNode(
            "ite6",
            str,
            condition_sources=(a_src, b_src),
            condition_fn=lambda a, b: a.value_or(False) and b.value_or(False),
            then_branch=then_src,
        )

        a_src.push(True, "user")
        b_src.push(True, "user")
        then_src.push("both_true", "user")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "both_true"

    @pytest.mark.asyncio
    async def test_condition_source_impossible_falls_to_else(self):
        """When condition_source is impossible, condition_fn handles it and returns False."""
        from dag.derived_node import DerivedNode

        class _FailingCond(DerivedNode[bool]):
            def __init__(self):
                super().__init__("fc", bool, ())
                self._attempt = Attempt.impossible("failed")

            def compute(self):
                return self._attempt

        cond = _FailingCond()
        then_src = UserInputNode[str]("then7", str)
        else_src = UserInputNode[str]("else7", str)

        node = IfThenElseNode(
            "ite7",
            str,
            condition_sources=(cond,),
            condition_fn=lambda a: a.succeeded and bool(a.value),  # safe for impossible
            then_branch=then_src,
            else_branch=else_src,
        )

        then_src.push("then_val", "user")
        else_src.push("else_val", "user")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "else_val"
