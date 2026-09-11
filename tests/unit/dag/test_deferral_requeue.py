"""Deterministic micro-tests of the deferral contract in the queue scheduler.

The queue's rule (2026-09-08): a node whose refresh finds pending deps
must never be silently dropped. Two wake paths exist, chosen by the dep
state at deferral time:

- The pending dep is queued work (a DerivedNode whose event is in the
  queue): the event is re-queued directly after the last queued dep, so
  the node retries the moment its inputs are ready — in the same drain.
- The pending dep is dormant (an unpushed user input, or a node parked
  behind one): re-queueing would spin the drain forever, so the node
  parks. The input's push signal owns the wake-up for the whole chain.
"""
from typing import override

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import AsyncQueueScheduler, flush_processor, get_scheduler
from dag.user_input_node import UserInputNode


class _Upper(DerivedNode[str]):
    def __init__(self, node_id, *, src):
        super().__init__(node_id, str, (src,))

    @override
    @staticmethod
    def compute(src: Attempt) -> Attempt:
        val = src.value_or_none()
        if val is None:
            return Attempt.pending()
        return Attempt.succeeded(str(val).upper())


class _Decorated(DerivedNode[str]):
    def __init__(self, node_id, *, src):
        super().__init__(node_id, str, (src,))

    @override
    def compute(self, src: Attempt) -> Attempt:
        val = src.value_or_none()
        if val is None:
            return Attempt.pending()
        return Attempt.succeeded(f"<<{val}>>")


class _PendingOnce(DerivedNode[str]):
    """Defers once even with satisfied deps, then succeeds."""

    def __init__(self, node_id, *, src):
        super().__init__(node_id, str, (src,))
        self.calls = 0

    @override
    def compute(self, src: Attempt) -> Attempt:
        self.calls += 1
        val = src.value_or_none()
        if val is None or self.calls == 1:
            return Attempt.pending()
        return Attempt.succeeded(str(val).upper())


def _sched_state() -> tuple[int, dict]:
    """(queue depth, scheduled-map) of the active scheduler."""
    sched = get_scheduler()
    assert isinstance(sched, AsyncQueueScheduler)
    return sched._queue.qsize(), dict(sched._scheduled)


@pytest.mark.asyncio
async def test_fresh_cascade_completes_in_one_flush_regardless_of_pop_order():
    """A (pushed), B on A, C on B — all fresh. ONE flush resolves C.

    If C pops before B it defers, is re-queued after B, and completes
    once B computes. If B pops first, C computes straight away. Either
    order must converge within the same drain.
    """
    a = UserInputNode("dr_a", str)
    b = _Upper("dr_b", src=a)
    c = _Decorated("dr_c", src=b)
    a.push("abc", "test")

    drained = await flush_processor()

    ca = await c.attempt()
    assert ca.succeeded, f"one flush must resolve the chain, got {ca.status}: {ca.error}"
    assert ca.value_or_none() == "<<ABC>>"
    assert drained >= 2


@pytest.mark.asyncio
async def test_parked_chain_wakes_from_one_push():
    """A unpushed: B and C park (no spin). Pushing A wakes the chain."""
    a = UserInputNode("pk_a", str)
    b = _Upper("pk_b", src=a)
    c = _Decorated("pk_c", src=b)

    await flush_processor()  # must terminate, everything parked

    assert (await b.attempt()).pending
    assert (await c.attempt()).pending

    a.push("abc", "test")
    await flush_processor()

    ba = await b.attempt()
    ca = await c.attempt()
    assert ba.succeeded, f"B must wake from the push, got {ba.status}"
    assert ca.succeeded and ca.value_or_none() == "<<ABC>>", (
        f"C must wake from the same push, got {ca.status}: {ca.error}"
    )


@pytest.mark.asyncio
async def test_drain_terminates_with_no_queue_residue():
    """A parked drain leaves the queue and the schedule map empty."""
    a = UserInputNode("rs_a", str)
    b = _Upper("rs_b", src=a)
    _Decorated("rs_c", src=b)

    await flush_processor()

    depth, scheduled = _sched_state()
    assert depth == 0, "parked nodes must not leave events queued"
    assert scheduled == {}, "parked nodes must not stay in the schedule map"


@pytest.mark.asyncio
async def test_compute_pending_with_satisfied_deps_waits_for_signal():
    """A bare re-flush must not spin on a compute-internal pending.

    A node that returns pending with satisfied deps has no queued dep to
    follow, so it parks. Only the next dep signal retries it.
    """
    a = UserInputNode("sg_a", str)
    b = _PendingOnce("sg_b", src=a)
    a.push("abc", "test")

    await flush_processor()
    assert b.calls == 1
    assert (await b.attempt()).pending

    await flush_processor()  # no push in between: no event, no retry
    assert b.calls == 1, "a parked node must not be retried by a bare flush"

    a.push("abc", "test")
    await flush_processor()
    ba = await b.attempt()
    assert ba.succeeded and ba.value_or_none() == "ABC"
    assert b.calls == 2
