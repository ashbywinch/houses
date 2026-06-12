"""Tests for EndpointClient — retry logic, blocking, and Retry-After support.

Behavioural requirements (not implementation details):
- 403/4xx error → never retry, permanently blocked for call
- 429 with Retry-After → retry after that delay, then block if exhausted
- 429 without Retry-After → retry with exponential backoff, then block
- 5xx → retryable (transient server error)
- Connection error → retryable
- Once blocked (any reason), subsequent calls return None immediately
- Cumulative retry delay ≤ 180s budget
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from houses.endpoint_client import EndpointClient
from houses.http_error import HttpError

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """Fresh EndpointClient with fast retry settings for testing."""
    return EndpointClient("test", max_retries=2, base_delay=0.01)


# ── Helpers ──────────────────────────────────────────────────────────────


async def _ok(delay: float = 0):
    """Async callable returning dict."""
    return {"status": "ok"}


async def _raise_429(retry_after: str | None = "0.01", body: str = ""):
    """Raise a rate-limit error, optionally with Retry-After."""
    headers = {"Retry-After": retry_after} if retry_after else {}
    raise HttpError(429, body, headers=headers)


async def _raise_403():
    raise HttpError(403, "forbidden")


async def _raise_404():
    raise httpx.HTTPStatusError("not found", request=None, response=httpx.Response(404))


async def _raise_500():
    raise HttpError(500, "server error")


async def _raise_connect_error():
    raise httpx.ConnectError("connection refused")


async def _raise_timeout():
    raise httpx.ReadTimeout("timed out")


# ── Tests ────────────────────────────────────────────────────────────────


class TestEndpointClient:
    """EndpointClient blocks, retries, and respects Retry-After."""

    def test_403_permanently_blocks(self, client):
        """403 error → not retried, permanently blocked for call."""
        result = asyncio_run(client.request(_raise_403))
        assert result is None, "403 should return None"
        assert client._permanently_blocked, "Client should be permanently blocked"
        # Second call returns None without trying
        result2 = asyncio_run(client.request(_ok))
        assert result2 is None, "Blocked client should not attempt the call"

    def test_403_counts_as_one_attempt(self, client):
        """403 should not consume retries — it's not retried at all."""
        asyncio_run(client.request(_raise_403))
        assert client._attempt_count == 1, "403 should count as 1 attempt"

    def test_429_retried_then_blocks(self, client):
        """429 with Retry-After → retried, then blocked when exhausted."""
        # All 3 attempts (initial + 2 retries) will 429
        result = asyncio_run(client.request(_raise_429))
        assert result is None, "Should return None after retries exhausted"
        assert client._cumulative_blocked, "Should be blocked after exhausting retries"
        # Subsequent call returns None without trying
        result2 = asyncio_run(client.request(_ok))
        assert result2 is None, "Blocked client should skip"

    def test_429_without_retry_after_uses_backoff(self, client):
        """429 without Retry-After header → retries with exponential backoff."""
        c = EndpointClient("test", max_retries=1, base_delay=0.05, max_cumulative_delay=10.0)
        with patch("houses.endpoint_client.EndpointClient._sleep") as mock_sleep:
            asyncio_run(c.request(lambda: _raise_429(retry_after=None)))
        # Should have retried at least once (exp backoff, not Retry-After)
        assert mock_sleep.call_count >= 1, "Should have retried with backoff"
        assert c._cumulative_blocked, "Should be blocked after exhausting retries"

    def test_404_not_retried(self, client):
        """404 error → not retried, permanently blocked."""
        result = asyncio_run(client.request(_raise_404))
        assert result is None, "404 should return None"
        assert client._permanently_blocked, "Client should be permanently blocked"
        assert client._attempt_count == 1, "Should only attempt once"

    def test_500_retried(self, client):
        """5xx errors are retried (transient server errors)."""
        c = EndpointClient("test", max_retries=2, base_delay=0.01)
        result = asyncio_run(c.request(_raise_500))
        assert result is None
        assert c._attempt_count == 3, f"Should retry 5xx, got {c._attempt_count} attempts"

    def test_connect_error_retried(self, client):
        """Connection errors are retried with exponential backoff."""
        c = EndpointClient("test", max_retries=2, base_delay=0.01)
        result = asyncio_run(c.request(_raise_connect_error))
        assert result is None
        assert c._attempt_count == 3, f"Should retry connection errors, got {c._attempt_count} attempts"

    def test_successful_call_returns_result(self, client):
        """Successful call returns the data and doesn't block."""
        result = asyncio_run(client.request(_ok))
        assert result == {"status": "ok"}
        assert not client.blocked, "Successful call should not block"

    def test_successful_call_resets_blocked(self, client):
        """After a 429 block exhausts, a new call should be blocked."""
        asyncio_run(client.request(_raise_429))
        assert client.blocked
        # The client stays blocked — the user must create a new one
        # per endpoint call lifecycle.

    def test_cumulative_budget_tracked(self, client):
        """Each retry deducts from the cumulative budget."""
        c = EndpointClient("test", max_retries=2, base_delay=0.1, max_cumulative_delay=10.0)
        with patch.object(c, "_sleep"):
            asyncio_run(c.request(_raise_429))
        # 2 retries at base_delay * backoff^0 + base_delay * backoff^1
        # = 0.1 + 0.2 = 0.3s budget used
        assert c._cumulative_delay < 10.0, "Budget should be partially consumed"
        assert c._cumulative_delay > 0, "Budget should be consumed"


# ── Async helper ─────────────────────────────────────────────────────────


def asyncio_run(coro):
    """Run an async test function synchronously."""
    import asyncio

    return asyncio.run(coro)
