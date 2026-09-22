"""Regression: staleness must be causal, not wall-clock.

The frozen-monthly-totals race (2026-09-08): a node resolved its inputs,
then spent time computing. While it computed, its dependency persisted a
new value. The node then persisted its STALE result and stamped its
bookkeeping with the dependency's NEW identity. Every wall-clock and
bookkeeping comparison afterwards says "fresh", yet the value is stale:
the completion stamp lies about which version of the world the node
consumed.

The change sequence (dag.node.next_change_seq) stamps the node at input
RESOLUTION, so the staleness check is causal: a dependency that
persisted after this node resolved its inputs makes it stale, whatever
the clock and bookkeeping say.
"""

import asyncio
from typing import override

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import flush_processor, get_scheduler
from dag.user_input_node import UserInputNode


class _SlowUpper(DerivedNode[str]):
    """Holds its inputs across an await, so the dependency can change
    between input resolution and persistence — the race window."""

    def __init__(self, node_id, *, src):
        super().__init__(node_id, str, (src,))

    @override
    async def compute(self, src: Attempt) -> Attempt:
        val = src.value_or_none()
        await asyncio.sleep(0.05)
        if val is None:
            return Attempt.pending()
        return Attempt.succeeded(str(val).upper())


@pytest.mark.asyncio
async def test_dep_persisting_during_compute_marks_node_stale():
    a = UserInputNode("ca_a", str)
    b = _SlowUpper("ca_b", src=a)
    a.push("one", "test")
    await flush_processor()
    assert (await b.attempt()).value_or_none() == "ONE"

    # Start the child's recompute, and change the dependency WHILE the
    # child holds its (now outdated) inputs mid-compute.
    refresh_task = asyncio.create_task(b.refresh())
    await asyncio.sleep(0.01)  # child has resolved inputs, is computing
    a.push("two", "test")  # persists AFTER the child's input resolution
    await refresh_task

    # The child persisted "ONE" while carrying the dep's new bookkeeping:
    # its completion stamp is newer than the dep's persist, and its
    # dep_timestamps entry matches the dep's current stamp. Only the
    # causal sequence can see the truth.
    assert b._is_stale(), (
        "a dependency that persisted while the node was computing (after "
        "input resolution) must mark the node stale — the completion "
        "stamp and refreshed bookkeeping both lie about the value held"
    )

    # And the graph converges: the next drain re-prices the child.
    get_scheduler().schedule(b)
    await flush_processor()
    assert (await b.attempt()).value_or_none() == "TWO"
