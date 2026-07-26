"""Test that previously-computed results load from DB on re-creation.

User-visible contract: a DerivedNode with any persisted status (succeeded,
impossible, pending+retry) must report that status immediately on re-creation
without waiting for the scheduler to re-process it.  If nodes stay pending
on restart, the frontend shows ``?`` instead of commute data.
"""

from __future__ import annotations

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import AsyncQueueScheduler, flush_processor, set_scheduler
from dag.user_input_node import UserInputNode


class _IntNode(DerivedNode[int]):
    def __init__(self, node_id: str, deps) -> None:
        super().__init__(node_id, int, deps)

    def compute(self, src: Attempt[int]) -> Attempt[int]:
        v = src.value_or_none()
        if v is None or v < 0:
            return Attempt.impossible("no value")
        return Attempt.succeeded(v * 2)


@pytest.mark.asyncio
async def test_succeeded_survives_recreation():
    """A succeeded node reports succeeded on restart, not pending."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[int]("surv_src", int)
    node = _IntNode("surv_node", deps=(src,))

    src.push(5, "test")
    await flush_processor()
    assert (await node.attempt()).value_or_none() == 10

    # Re-create (simulate restart)
    src2 = UserInputNode[int]("surv_src", int)
    node2 = _IntNode("surv_node", deps=(src2,))
    a = await node2.attempt()
    assert a.succeeded, f"succeeded on reload, got status={a.status}"
    assert a.value_or_none() == 10


@pytest.mark.asyncio
async def test_impossible_survives_recreation():
    """An impossible node reports impossible on restart, not pending.

    If this fails, every impossible node gets re-queued on every server
    restart, the scheduler thrashes re-processing them, and the frontend
    shows ``?`` for minutes before the node resolves.
    """
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[int]("imps_src", int)
    node = _IntNode("imps_node", deps=(src,))

    # Push a negative value so compute() returns impossible
    src.push(-1, "test")
    await flush_processor()
    a = await node.attempt()
    assert a.impossible, f"Phase 1 should be impossible, got {a.status}"

    # Re-create (simulate restart)
    src2 = UserInputNode[int]("imps_src", int)
    node2 = _IntNode("imps_node", deps=(src2,))

    a2 = await node2.attempt()
    assert a2.impossible, (
        f"impossible on reload, got status={a2.status}. "
        f"If pending, the persisted result was not loaded and the node "
        f"will be re-queued by the scheduler — commutes stay missing."
    )


@pytest.mark.asyncio
async def test_impossible_not_re_queued_on_register():
    """An impossible-loaded node must NOT be scheduled by register()."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[int]("nrq_src", int)
    node = _IntNode("nrq_node", deps=(src,))
    src.push(-1, "test")
    await flush_processor()
    assert (await node.attempt()).impossible

    # Re-create with a fresh scheduler so we can check queue emptiness
    src2 = UserInputNode[int]("nrq_src", int)
    fresh_sched = AsyncQueueScheduler(respect_time=False)
    set_scheduler(fresh_sched)
    node2 = _IntNode("nrq_node", deps=(src2,))

    assert (await node2.attempt()).impossible
    assert fresh_sched._queue.empty(), "must not queue an impossible-loaded node"
