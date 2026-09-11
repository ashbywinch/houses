"""Regression: a dependency persisting after a node's last compute marks it stale.

The frozen-monthly-totals incident (2026-09-08): a node's value fell
behind its dependencies while their rows moved underneath it, and the
node kept reporting its outdated result as fresh. Staleness in this DAG
is the timestamp comparison ``dep._persisted_at > self._computed_at``
(dag/derived_node.py ``_is_stale``): however the bookkeeping looks, a
dependency that has persisted since this node last computed makes the
node stale, and only a drain re-prices it.

Deterministic on purpose — push, observe, drain. No sleeps, no events,
no races: the contract is the ordering of persists and computes, which
the flush controls exactly.
"""

from typing import override

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode


class _Upper(DerivedNode[str]):
    """A trivial derived node: upper-cases its input."""

    def __init__(self, node_id, *, src):
        super().__init__(node_id, str, (src,))

    @override
    async def compute(self, src: Attempt) -> Attempt:
        val = src.value_or_none()
        if val is None:
            return Attempt.pending()
        return Attempt.succeeded(str(val).upper())


@pytest.mark.asyncio
async def test_a_dependency_persisted_after_the_last_compute_marks_the_node_stale():
    a = UserInputNode("ca_a", str)
    b = _Upper("ca_b", src=a)
    a.push("one", "test")
    await flush_processor()
    assert (await b.attempt()).value_or_none() == "ONE"
    assert not b._is_stale()

    # The dependency persists AFTER b's last compute: b now holds an
    # outdated result, and staleness must say so immediately.
    a.push("two", "test")
    assert b._is_stale(), (
        "a dependency that persisted after the node's last compute must "
        "mark the node stale — it reports an outdated value as fresh "
        "until a drain re-prices it"
    )

    # The next drain converges: b recomputes from the new input.
    await flush_processor()
    assert (await b.attempt()).value_or_none() == "TWO"
    assert not b._is_stale()