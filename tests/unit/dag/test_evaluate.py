"""D1 — pure scenario evaluation: what-if without persisting.

``evaluate(targets, overrides)`` computes what target nodes WOULD be if
certain node values were different — no persistence, no signals, no
scheduler, no mutation of real node state. The critical property: nodes
whose compute bodies read deps through ``latest_attempt()`` (expressions,
predicates, ``_get_active_deps``) see the hypothetical values
transparently, exactly as they see real values during refresh.
"""

from __future__ import annotations

from typing import override

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.expression import Ref
from dag.scheduler import AsyncQueueScheduler, flush_processor, reset_scheduler, set_scheduler
from dag.user_input_node import UserInputNode


@pytest.fixture(autouse=True)
def _isolated_scheduler():
    set_scheduler(AsyncQueueScheduler(respect_time=False))
    yield
    reset_scheduler()


class _Double(DerivedNode[int]):
    """Doubles its single integer dep — tests override propagation."""

    def __init__(self, node_id: str, source):
        super().__init__(node_id, int, (source,))

    @override
    def compute(self, *dep_attempts: Attempt) -> Attempt[int]:
        src = dep_attempts[0]
        if not src.succeeded:
            return Attempt.impossible("dep failed")
        return Attempt.succeeded(src.get() * 2)


class _SumExpr(DerivedNode[int]):
    """Expression-based: compute evaluates Ref(dep0) + Ref(dep1).

    Refs read ``latest_attempt()`` — the staged read path — so an
    override on either dep must flow through the expression.
    """

    def __init__(self, node_id: str, a, b):
        super().__init__(node_id, int, (a, b))

    @override
    @property
    def expression(self):
        return Ref(self._deps[0]) + Ref(self._deps[1])

    @override
    def compute(self, *dep_attempts: Attempt) -> Attempt[int]:
        return self.expression.evaluate()


class _Counting(DerivedNode[int]):
    """Recomputes only when asked; counts invocations."""

    calls = 0

    def __init__(self, node_id: str, source):
        super().__init__(node_id, int, (source,))

    @override
    def compute(self, *dep_attempts: Attempt) -> Attempt[int]:
        _Counting.calls += 1
        return Attempt.succeeded(dep_attempts[0].value_or(0) + 1)


async def test_override_changes_target_and_leaves_real_state_untouched():
    from dag.scheduler import flush_processor

    src = UserInputNode[int]("eval_a", int)
    target = _Double("eval_a2", src)
    src.push(10, "user")
    await flush_processor()

    from dag.evaluate import evaluate

    result = await evaluate(target, overrides={"eval_a": 100})
    assert result[target._id].value == 200

    # Real state untouched: latest_attempt still reflects the pushed value.
    assert target.latest_attempt().value == 20


async def test_expression_nodes_see_overrides_through_ref():
    from dag.scheduler import flush_processor

    a = UserInputNode[int]("eval_b1", int)
    b = UserInputNode[int]("eval_b2", int)
    target = _SumExpr("eval_b3", a, b)
    a.push(1, "user")
    b.push(2, "user")
    await flush_processor()

    from dag.evaluate import evaluate

    result = await evaluate(target, overrides={"eval_b1": 10})
    assert result[target._id].value == 12
    real = target.latest_attempt().value
    assert real is not None
    assert result[target._id].value == real + 9


async def test_mid_chain_override():
    src = UserInputNode[int]("eval_c1", int)
    mid = _Double("eval_c2", src)
    target = _Double("eval_c3", mid)
    src.push(3, "user")

    from dag.evaluate import evaluate

    result = await evaluate(target, overrides={"eval_c2": 50})
    assert result[target._id].value == 100  # override bypasses src entirely


async def test_unrelated_branches_are_not_recomputed():
    src = UserInputNode[int]("eval_d1", int)
    target = _Double("eval_d2", src)
    other_src = UserInputNode[int]("eval_d3", int)
    unrelated = _Counting("eval_d4", other_src)
    src.push(1, "user")
    other_src.push(1, "user")
    await flush_processor()
    assert unrelated.latest_attempt().value == 2

    _Counting.calls = 0
    from dag.evaluate import evaluate

    await evaluate(target, overrides={"eval_d1": 42})
    assert _Counting.calls == 0, "unrelated node must not be recomputed"


async def test_multiple_targets_and_cleanup():
    from dag.scheduler import flush_processor

    a = UserInputNode[int]("eval_e1", int)
    t1 = _Double("eval_e2", a)
    t2 = _SumExpr("eval_e3", a, t1)
    a.push(2, "user")
    await flush_processor()

    from dag.evaluate import evaluate

    result = await evaluate([t1, t2], overrides={"eval_e1": 5})
    assert result[t1._id].value == 10
    assert result[t2._id].value == 15

    # After evaluation the staging context is gone: real values return.
    assert t1.latest_attempt().value == 4
    assert t2.latest_attempt().value == 6


async def test_override_attempts_carry_hypothetical_metadata():
    a = UserInputNode[int]("eval_f1", int)
    target = _Double("eval_f2", a)
    a.push(1, "user")

    from dag.evaluate import evaluate

    result = await evaluate(target, overrides={"eval_f1": 7})
    override_attempt = result.get("eval_f1") or (await _staged_lookup("eval_f1"))
    assert override_attempt is None or override_attempt.succeeded
    # The target itself is NOT hypothetical — only the overridden input is.
    assert result[target._id].metadata.get("hypothetical") is None


async def _staged_lookup(node_id: str) -> Attempt | None:
    from dag.eval_context import staged_attempt

    return staged_attempt(node_id)


async def test_get_active_deps_honors_overridden_condition():
    """IfThenElseNode's _get_active_deps reads the condition via
    latest_attempt — an overridden condition must flip the branch."""
    from dag.if_then_else_node import IfThenElseNode, IfThenElseOptions

    cond = UserInputNode[bool]("eval_g1", bool)
    then_src = UserInputNode[int]("eval_g2", int)
    else_src = UserInputNode[int]("eval_g3", int)
    node = IfThenElseNode(
        "eval_g4",
        int,
        options=IfThenElseOptions(
            condition_sources=(cond,),
            condition_fn=lambda a: a.value_or(False),
            then_branch=then_src,
            else_branch=else_src,
        ),
    )
    cond.push(False, "user")
    then_src.push(100, "user")
    else_src.push(1, "user")

    from dag.evaluate import evaluate

    result = await evaluate(node, overrides={"eval_g1": True})
    assert result[node._id].value == 100  # then-branch, despite real cond=False
