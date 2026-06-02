"""Async retry with exponential backoff and jitter."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from functools import wraps

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

    Usage inside a function body:
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
                delay = min(base_delay * (backoff ** attempt), max_delay)
                if jitter:
                    delay *= 0.5 + random.random() * 0.5  # 50-100% of computed delay
                logger.warning(
                    "Attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt + 1, max_retries + 1, e, delay,
                )
                await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff: float = 2.0,
    jitter: bool = True,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """Decorator: wrap an async function with retry logic."""
    def decorator(func: Callable[..., Awaitable]):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await retry_async(
                lambda: func(*args, **kwargs),
                max_retries=max_retries,
                base_delay=base_delay,
                max_delay=max_delay,
                backoff=backoff,
                jitter=jitter,
                exceptions=exceptions,
            )
        return wrapper
    return decorator
