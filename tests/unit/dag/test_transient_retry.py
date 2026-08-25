"""Test that compute() only schedules retry for transient errors, not permanent ones.

User-visible contract: a 403 (forbidden) should produce ``impossible`` immediately,
not stay ``pending`` forever retrying. The frontend shows ``?`` for pending nodes;
users must see clear failures, not infinite spinners.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import AsyncQueueScheduler, flush_processor, set_scheduler
from dag.user_input_node import UserInputNode
from houses.helpers import is_transient_error


@pytest.mark.asyncio
async def test_non_transient_403_does_not_schedule_retry():
    """A compute() that catches httpx.HTTPStatusError(403) should NOT
    schedule a retry — 403 is permanent and the node goes impossible."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[str]("nt403_src", str)
    node = _CatchAndDecideNode("nt403_result", deps=(src,))
    node.error_to_raise = httpx.HTTPStatusError(
        "forbidden",
        request=None,
        response=httpx.Response(403),
    )

    src.push("go", "test")
    await flush_processor()

    a = await node.attempt()
    assert a.impossible, (
        f"Expected impossible for 403, got pending={a.pending}. 403 is permanent — compute() must NOT schedule retry."
    )
    assert node.schedule_retry_count == 0, "schedule_retry called for non-transient error"


@pytest.mark.asyncio
async def test_transient_429_schedules_retry():
    """A 429 (rate limit) caught in compute() schedules a retry and returns pending."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[str]("t429_src", str)
    node = _CatchAndDecideNode("t429_result", deps=(src,))
    node.error_to_raise = httpx.HTTPStatusError(
        "rate limited",
        request=None,
        response=httpx.Response(429, headers={"Retry-After": "10"}),
    )

    src.push("go", "test")
    await flush_processor()

    a = await node.attempt()
    assert a.pending, (
        f"Expected pending for 429, got impossible={a.impossible}. 429 is transient — compute() MUST schedule retry."
    )
    assert node.schedule_retry_count == 1


@pytest.mark.asyncio
async def test_transient_500_schedules_retry():
    """A 500 (server error) schedules a retry."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[str]("t500_src", str)
    node = _CatchAndDecideNode("t500_result", deps=(src,))
    node.error_to_raise = httpx.HTTPStatusError(
        "server error",
        request=None,
        response=httpx.Response(500),
    )

    src.push("go", "test")
    await flush_processor()

    a = await node.attempt()
    assert a.pending, f"Expected pending for 500, got impossible={a.impossible}"
    assert node.schedule_retry_count == 1


class _CatchAndDecideNode(DerivedNode[str]):
    """A node whose compute() catches errors and decides retry vs impossible
    using the same pattern as TransitNode/WalkabilityNode."""

    def __init__(self, node_id: str, deps) -> None:
        super().__init__(node_id, str, deps)
        self.error_to_raise: Exception | None = None
        self.schedule_retry_count = 0

    def schedule_retry(self, delay: timedelta) -> None:  # type: ignore[override]  # test double: replaces the base -> bool contract with a call-counting stub (return value never consumed here) and omits the repo's @override decorator — the guard targets production dispatch, not test fixtures
        self.schedule_retry_count += 1

    def compute(self, src: Attempt[str]) -> Attempt[str]:
        if self.error_to_raise is not None:
            exc = self.error_to_raise
            if is_transient_error(exc):
                self.schedule_retry(self._retry_delay_from(exc))
                return Attempt.pending()
            return Attempt.impossible(f"{self._id}: {exc}")
        return Attempt.succeeded("ok")


@pytest.mark.asyncio
async def test_returned_retryable_impossible_schedules_retry():
    """A service that CATCHES a transient error and RETURNS
    Attempt.impossible(msg) with error_info.retryable (instead of
    raising) must still schedule a DAG retry — the retry decision must
    not depend on whether the service raised or returned."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[str]("rt_src", str)
    node = _ReturnImpossibleNode("rt_result", deps=(src,))

    src.push("go", "test")
    await flush_processor()

    a = await node.attempt()
    assert a.pending, f"Expected pending for returned retryable impossible, got impossible={a.impossible}"
    assert node.schedule_retry_count == 1


@pytest.mark.asyncio
async def test_returned_permanent_impossible_does_not_retry():
    """A returned impossible WITHOUT retryable must stay impossible."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[str]("rp_src", str)
    node = _ReturnImpossibleNode("rp_result", deps=(src,), permanent=True)

    src.push("go", "test")
    await flush_processor()

    a = await node.attempt()
    assert a.impossible, f"Expected impossible, got pending={a.pending}"
    assert node.schedule_retry_count == 0


class _ReturnImpossibleNode(DerivedNode[str]):
    """A node whose compute() returns Attempt.impossible built inside an
    except block — exercising the AttemptError auto-capture path."""

    def __init__(self, node_id: str, deps, permanent: bool = False) -> None:
        super().__init__(node_id, str, deps)
        self.permanent = permanent
        self.schedule_retry_count = 0

    def schedule_retry(self, delay: timedelta) -> bool:  # type: ignore[override]  # test double: call-counting stub for the returned-impossible path; omits the repo's @override decorator, which guards production node dispatch, not test fixtures
        self.schedule_retry_count += 1
        return True

    def compute(self, src: Attempt[str]) -> Attempt[str]:
        if self.permanent:
            return Attempt.impossible("permanent failure")
        try:
            raise httpx.HTTPStatusError(
                "rate limited",
                request=None,
                response=httpx.Response(429, headers={"Retry-After": "10"}),
            )
        except httpx.HTTPStatusError:
            return Attempt.impossible("transit API rate limited")
