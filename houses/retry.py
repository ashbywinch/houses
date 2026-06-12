"""Async retry with exponential backoff, jitter, and Retry-After support.

If a caught exception has a ``retry_after`` attribute (e.g. from
:class:`houses.http_error.HttpError`), that value is used as the delay
instead of exponential backoff.  This allows rate-limited APIs to tell
us exactly when to retry.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def retry_async(
    fn: Callable[..., Awaitable],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff: float = 2.0,
    jitter: bool = True,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Awaitable:
    """Retry an async call with exponential backoff and jitter.

    If the exception has a ``retry_after`` attribute (e.g.
    :class:`houses.http_error.HttpError` with a Retry-After header), that
    value is used as the delay instead of exponential backoff.

    Usage inside a function body::

        result = await retry_async(
            lambda: client.post(url, ...),
            max_retries=3, base_delay=1.0,
        )
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except exceptions as e:
            last_exc = e
            if attempt < max_retries:
                # Use Retry-After if the API told us when to retry.
                retry_after = getattr(e, "retry_after", None)
                if retry_after is not None:
                    delay = min(retry_after, max_delay)
                else:
                    delay = min(base_delay * (backoff**attempt), max_delay)
                    if jitter:
                        delay *= 0.5 + random.random() * 0.5
                logger.warning(
                    "Attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt + 1,
                    max_retries + 1,
                    e,
                    delay,
                )
                await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]
