"""Boundary guards for code whose failure modes are unbounded.

A subclass ``compute``, a user-supplied callable, and a third-party provider
can raise anything at all. Each such call routes through ONE guard here, so
the boundary policy — degrade once, never leak — is decided in one place
instead of re-decided at every call site. Callers that must distinguish
landed from failed use :func:`capture_async` (loop boundaries deciding
control flow rather than substituting a value).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

type Outcome[T] = tuple[T | None, Exception | None]


def guarded[T](call: Callable[[], T], on_error: Callable[[Exception], T]) -> T:
    """Run ``call``; a failure converts through ``on_error`` (never leaks)."""
    try:
        return call()
    # lucidlint: ignore broad-except the call is subclass/user code — one failure degrades, never leaks
    except Exception as exc:
        return on_error(exc)


def capture[T](call: Callable[[], T]) -> Outcome[T]:
    """Run ``call``; return ``(value, None)`` or ``(None, failure)``.

    For boundaries that must decide control flow — return an impossible
    attempt, skip a render — rather than substitute a fallback value.
    """
    try:
        return call(), None
    # lucidlint: ignore broad-except the call is user/subclass code — the failure is returned, never hidden
    except Exception as exc:
        return None, exc


async def guarded_async[T](
    call: Callable[[], Awaitable[T]],
    on_error: Callable[[Exception], T] | Callable[[Exception], Awaitable[T]],
    *,
    reraise: tuple[type[BaseException], ...] = (),
) -> T:
    """Await ``call``; a failure converts through ``on_error``.

    ``reraise`` names the classes a caller handles at a higher tier — e.g.
    transient HTTP failures the DAG retries at the provider's interval —
    those propagate unchanged; every other failure degrades here.
    """
    try:
        return await call()
    except reraise:
        raise
    # lucidlint: ignore broad-except the call is provider/subclass code — one failure degrades, never leaks
    except Exception as exc:
        fallback = on_error(exc)
        if inspect.isawaitable(fallback):
            return await fallback
        return fallback


async def capture_async[T](
    call: Callable[[], Awaitable[T]],
    *,
    reraise: tuple[type[BaseException], ...] = (),
) -> Outcome[T]:
    """Await ``call``; return ``(value, None)`` or ``(None, failure)``.

    For boundaries that must decide control flow — break a connection loop,
    skip a requeue — rather than substitute a fallback value. ``reraise``
    names the classes a caller handles at a higher tier; those propagate.
    """
    try:
        return await call(), None
    except reraise:
        raise
    # lucidlint: ignore broad-except the call is node/provider code — the failure is returned, never hidden
    except Exception as exc:
        return None, exc
