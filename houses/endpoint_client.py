"""Reusable client for a single API endpoint with Retry-After support.

Tracks retry state across multiple calls within the same request lifecycle
(e.g. a ``/properties`` batch).  Once retries are exhausted the API is
skipped for the rest of the batch — we never burn budget hitting a rate
limit that won't self-resolve.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from houses.http_error import HttpError

logger = logging.getLogger(__name__)


class EndpointClient:
    """Reusable client for a single API endpoint.

    On 429 with Retry-After: respects the specified delay.
    On 429 without Retry-After: falls back to exponential backoff.
    On 4xx (except 429): permanently blocked (won't self-resolve).
    On 5xx / connection errors: retried with exponential backoff.

    Once retries are exhausted, the client is blocked for the
    remainder of the request lifecycle.

    Usage::

        _google = EndpointClient("google-routes", max_retries=2, base_delay=2.0)

        async def _do_post():
            resp = await client.post(url, ...)
            if resp.status_code == 429:
                raise HttpError(429, "rate limited", headers=dict(resp.headers))
            resp.raise_for_status()
            return resp.json()

        data = await _google.request(_do_post)
    """

    def __init__(
        self,
        name: str,
        *,
        max_retries: int = 3,
        base_delay: float = 2.0,
        backoff: float = 2.0,
        max_cumulative_delay: float = 180.0,
    ) -> None:
        self.name = name
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.backoff = backoff
        self.max_cumulative_delay = max_cumulative_delay

        # Internal state
        self._blocked_until: float = 0.0
        self._permanently_blocked: bool = False
        self._cumulative_blocked: bool = False
        self._attempt_count: int = 0
        self._cumulative_delay: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def blocked(self) -> bool:
        """True if the client should skip the API without trying."""
        return (
            self._permanently_blocked
            or self._cumulative_blocked
            or time.time() < self._blocked_until
        )

    async def request(
        self,
        fn: Callable[[], Awaitable[Any]],
    ) -> Any | None:
        """Execute *fn*, retrying on rate limits / connection errors.

        *fn* should raise ``HttpError`` (for HTTP errors with structured
        metadata), ``httpx.HTTPStatusError`` (for non-429 HTTP errors),
        or connection errors.  Returns the response dict on success, or
        ``None`` if all retries were exhausted or the request is blocked.

        Usage inside a module::

            data = await client.request(_do_post)
        """
        if self.blocked:
            logger.info("Skipping %s — blocked", self.name)
            return None

        for attempt in range(self.max_retries + 1):
            self._attempt_count = attempt + 1
            delay = 0.0
            retry_reason = ""
            should_retry = False

            try:
                return await fn()
            except HttpError as e:
                if e.status == 429:
                    retry_after = e.retry_after
                    if retry_after is not None:
                        delay = min(retry_after, self._remaining_budget)
                        retry_reason = f"Retry-After ({retry_after}s)"
                    else:
                        delay = self._backoff_delay(attempt)
                        retry_reason = f"backoff ({delay:.1f}s)"
                    should_retry = attempt < self.max_retries
                    self._blocked_until = time.time() + delay
                    logger.warning(
                        "%s: HTTP 429 on attempt %d/%d → %s. "
                        "Blocked for %.1fs (%.1fs budget remaining)",
                        self.name, attempt + 1, self.max_retries + 1,
                        retry_reason, delay, self._remaining_budget,
                    )
                elif 400 <= e.status < 500:
                    # 4xx (except 429) — won't self-resolve
                    self._permanently_blocked = True
                    logger.warning(
                        "%s: HTTP %d on attempt %d → permanently blocked",
                        self.name, e.status, attempt + 1,
                    )
                    return None
                else:
                    # 5xx — transient, retry
                    delay = self._backoff_delay(attempt)
                    should_retry = attempt < self.max_retries
                    logger.warning(
                        "%s: HTTP %d on attempt %d/%d → retrying in %.1fs",
                        self.name, e.status, attempt + 1,
                        self.max_retries + 1, delay,
                    )
            except httpx.HTTPStatusError as e:
                if 400 <= e.response.status_code < 500:
                    self._permanently_blocked = True
                    logger.warning(
                        "%s: HTTP %d on attempt %d → permanently blocked",
                        self.name, e.response.status_code, attempt + 1,
                    )
                    return None
                else:
                    delay = self._backoff_delay(attempt)
                    should_retry = attempt < self.max_retries
                    logger.warning(
                        "%s: HTTP %d on attempt %d/%d → retrying in %.1fs",
                        self.name, e.response.status_code, attempt + 1,
                        self.max_retries + 1, delay,
                    )
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadTimeout) as e:
                delay = self._backoff_delay(attempt)
                should_retry = attempt < self.max_retries
                logger.warning(
                    "%s: %s on attempt %d/%d → retrying in %.1fs",
                    self.name, type(e).__name__, attempt + 1,
                    self.max_retries + 1, delay,
                )
            except Exception as e:
                logger.warning(
                    "%s: %s on attempt %d — not retried",
                    self.name, type(e).__name__, attempt + 1,
                )
                return None

            if should_retry:
                self._cumulative_delay += delay
                await self._sleep(delay)
            else:
                break

        # Retries exhausted
        if self._cumulative_delay >= self.max_cumulative_delay:
            logger.warning(
                "%s: cumulative retry delay %.1fs exceeds budget %.1fs — blocked",
                self.name, self._cumulative_delay, self.max_cumulative_delay,
            )
        else:
            logger.warning(
                "%s: exhausted %d retries (%.1fs total delay) — blocked",
                self.name, self.max_retries + 1, self._cumulative_delay,
            )
        self._cumulative_blocked = True
        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _sleep(self, seconds: float) -> None:
        """Sleep for *seconds* (overridable in tests via patch)."""
        await asyncio.sleep(seconds)

    @property
    def _remaining_budget(self) -> float:
        return max(0.0, self.max_cumulative_delay - self._cumulative_delay)

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter."""
        delay = min(self.base_delay * (self.backoff**attempt), self._remaining_budget)
        jitter = 0.5 + random.random() * 0.5
        return delay * jitter
