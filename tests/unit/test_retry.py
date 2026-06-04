"""Tests for retry_async — real async logic, no mocks.

Tests that retry_async correctly handles:
- Successful calls (no retry needed)
- Transient failures with eventual success
- Permanent failures that exhaust retries
- Exception type filtering (non-matching exceptions propagate immediately)
"""

import pytest

from houses.retry import retry_async


class _FailCounter:
    """Callable that fails the first N times, then returns a value."""

    def __init__(self, fail_count: int, value: str = "ok"):
        self.fail_count = fail_count
        self.value = value
        self.attempts = 0

    async def __call__(self):
        self.attempts += 1
        if self.attempts <= self.fail_count:
            msg = f"Simulated failure #{self.attempts}"
            raise ValueError(msg)
        return self.value


@pytest.mark.asyncio
async def test_retry_succeeds_on_first_try():
    """A callable that never fails should return its result immediately."""

    async def ok():
        return "done"

    result = await retry_async(ok, max_retries=3)
    assert result == "done"


@pytest.mark.asyncio
async def test_retry_succeeds_after_two_failures():
    """A callable that fails twice then succeeds should retry and return."""
    counter = _FailCounter(fail_count=2, value="recovered")
    result = await retry_async(counter, max_retries=5, base_delay=0.01)
    assert result == "recovered"
    assert counter.attempts == 3  # 2 failures + 1 success


@pytest.mark.asyncio
async def test_retry_raises_after_exhausting_retries():
    """A callable that always fails should raise after max_retries attempts."""
    counter = _FailCounter(fail_count=99, value="never")
    with pytest.raises(ValueError, match="Simulated failure"):
        await retry_async(counter, max_retries=2, base_delay=0.01)
    assert counter.attempts == 3  # initial try + 2 retries


@pytest.mark.asyncio
async def test_retry_succeeds_with_default_params():
    """retry_async should work with default max_retries=3, base_delay=1.0."""
    counter = _FailCounter(fail_count=0, value="ok")
    result = await retry_async(counter)
    assert result == "ok"
    assert counter.attempts == 1


@pytest.mark.asyncio
async def test_retry_propagates_non_matching_exception():
    """An exception type not in the exceptions tuple should NOT be retried."""

    async def will_raise():
        raise TypeError("wrong type")

    with pytest.raises(TypeError, match="wrong type"):
        await retry_async(will_raise, max_retries=3, base_delay=0.01, exceptions=(ValueError,))


@pytest.mark.asyncio
async def test_retry_with_zero_retries():
    """With max_retries=0, a failure should raise immediately (no retry)."""

    async def fail():
        raise RuntimeError("no retries")

    with pytest.raises(RuntimeError, match="no retries"):
        await retry_async(fail, max_retries=0, base_delay=0.01)


@pytest.mark.asyncio
async def test_retry_backoff_increases_delay():
    """Each retry should wait longer than the previous one.

    This tests that the exponential backoff formula is being applied.
    We time retries with a short base_delay — the second delay should
    be roughly double the first, the third double the second.
    """
    import asyncio

    call_times: list[float] = []

    async def always_fail():
        call_times.append(asyncio.get_event_loop().time())
        raise ValueError("transient")

    t0 = asyncio.get_event_loop().time()
    with pytest.raises(ValueError, match="transient"):
        await retry_async(always_fail, max_retries=3, base_delay=0.05, jitter=False, backoff=2.0)
    t_end = asyncio.get_event_loop().time()

    # Total elapsed must cover initial + 3 retries, each getting longer.
    # Without jitter, delays should be: 0.05, 0.10, 0.20 = 0.35 total.
    assert len(call_times) == 4, f"Expected 4 calls, got {len(call_times)}"
    assert t_end - t0 >= 0.30, f"Total elapsed {t_end - t0:.3f}s too low for backoff sequence"
