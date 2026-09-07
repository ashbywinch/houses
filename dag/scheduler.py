"""Pluggable refresh scheduler for DerivedNodes.

Production: ``AsyncQueueScheduler`` — ONE FIFO queue drained by the DAG
processor thread, which owns a private asyncio loop for the async compute
functions. The uvicorn event loop never runs cascade work; it enqueues
work and reads in-memory snapshots.

Tests: inject an isolated instance via ``set_scheduler()`` — no thread;
``flush_processor()`` drains synchronously on the test thread.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import inspect
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, override

if TYPE_CHECKING:
    from dag.derived_node import DerivedNode

logger = logging.getLogger(__name__)


@dataclass(order=True)
class QueueEvent:
    scheduled_at: float
    node_id: str = field(compare=False)
    node: DerivedNode = field(compare=False, repr=False)


# ── Refresh scheduler (DI via ContextVar) ────────────────────────────


class RefreshScheduler:
    """Pluggable scheduler for refreshing stale DerivedNodes.

    Production: ``AsyncQueueScheduler`` — background ``asyncio.PriorityQueue``.
    Tests: provide an isolated instance with ``set_scheduler()``.
    """

    # Set per-instance via set_after_refresh(); AsyncQueueScheduler.after_refresh
    # delegates to it when non-None.
    _after_refresh_callback: Callable[[DerivedNode], object] | None = None

    @staticmethod
    def register(node: DerivedNode) -> None:
        """Called when a DerivedNode is created."""

    @staticmethod
    def unregister(node: DerivedNode) -> None:
        """Called when a DerivedNode is disconnected (cleanup)."""

    @staticmethod
    def registered_nodes() -> dict[str, DerivedNode]:
        """Every DerivedNode currently registered, by node id.

        The default scheduler does not track nodes; production's
        AsyncQueueScheduler does (used for admin force-regeneration).
        """
        return {}

    @staticmethod
    def schedule(node: DerivedNode) -> None:
        """Request that *node* be refreshed when convenient."""

    @staticmethod
    def schedule_at(node: DerivedNode, dt: datetime) -> None:
        """Schedule *node* for refresh at wall-clock time *dt*."""

    @staticmethod
    async def process_pending() -> int:
        """Synchronously process all currently scheduled nodes; returns the count processed."""
        return 0

    @staticmethod
    def after_refresh(node: DerivedNode) -> None:
        """Called after a node completes refresh (no-op default)."""


class AsyncQueueScheduler(RefreshScheduler):
    """Production scheduler — background ``asyncio.PriorityQueue``.

    When *respect_time* is True (default), events scheduled in the future
    are deferred until their wall-clock time arrives.  When False (test mode),
    every event executes immediately on ``process_pending()``.
    """

    @property
    def enqueued_since_flush(self) -> int:
        """Work items enqueued since the last complete drain (flush guard)."""
        return self._enqueued_since_flush

    @property
    def queue_depth(self) -> int:
        """Pending work items (operator visibility)."""
        self._ensure_primitives()
        queue, wakeup = self._queue, self._wakeup
        assert queue is not None and wakeup is not None
        return queue.qsize() + (1 if wakeup.is_set() else 0)

    @property
    def wakeup_set(self) -> bool:
        """Whether the processor was woken for pending work."""
        self._ensure_primitives()
        wakeup = self._wakeup
        assert wakeup is not None
        return wakeup.is_set()

    def __init__(self, respect_time: bool = True) -> None:
        # The queue and wakeup are created LAZILY, on whichever event loop
        # will actually drain them (the processor loop in production, the
        # test loop in tests). Constructing them eagerly binds asyncio
        # primitives to a loop that may never run — the processor then
        # blocks forever on its first queue.get() and nothing drains.
        self._queue: asyncio.PriorityQueue[QueueEvent] | None = None
        self._wakeup: asyncio.Event | None = None
        self._scheduled: dict[str, QueueEvent] = {}
        self._registered: dict[str, DerivedNode] = {}
        self._after_refresh_callback: Callable[[DerivedNode], object] | None = None
        self._respect_time: bool = respect_time
        self._enqueued_since_flush = 0
        """Work items enqueued since the last complete drain — the conftest
        flush_all() no-op guard reads it."""

    @override
    def register(self, node: DerivedNode) -> None:
        """Called when a node is created. Schedules it at its stored retry time or immediately."""
        self._registered[node._id] = node
        if node._id in self._scheduled:
            return
        if node._retry_at is not None and node._retry_at > datetime.now(UTC):
            self.schedule_at(node, node._retry_at)
        elif node._attempt.pending or node._is_stale():
            self.schedule(node)

    @override
    def unregister(self, node: DerivedNode) -> None:
        """Called when a node is disconnected (cleanup)."""
        self._scheduled.pop(node._id, None)
        self._registered.pop(node._id, None)

    @override
    def registered_nodes(self) -> dict[str, DerivedNode]:
        """Every DerivedNode currently registered, by node id."""
        return dict(self._registered)

    def _ensure_primitives(self) -> None:
        if self._queue is None:
            self._queue = asyncio.PriorityQueue()
        if self._wakeup is None:
            self._wakeup = asyncio.Event()

    def _put_event(self, event: QueueEvent) -> None:
        # Runs ON the draining loop (or inline in single-threaded
        # contexts) — never cross-thread on an asyncio primitive.
        self._ensure_primitives()
        queue, wakeup = self._queue, self._wakeup
        assert queue is not None and wakeup is not None
        queue.put_nowait(event)
        wakeup.set()

    @override
    def _enqueue(self, node: DerivedNode, scheduled_at: float) -> None:
        """Queue the node unless already queued, then wake the processor.

        Thread-safe by routing through the draining loop: asyncio
        primitives must only be touched on the loop that owns them."""
        if node._id in self._scheduled:
            return
        event = QueueEvent(scheduled_at=scheduled_at, node_id=node._id, node=node)
        self._scheduled[node._id] = event
        self._enqueued_since_flush += 1
        loop = _processor_loop
        if loop is not None:
            loop.call_soon_threadsafe(self._put_event, event)
        else:
            self._put_event(event)

    @override
    def schedule(self, node: DerivedNode) -> None:
        """Schedule for immediate processing. No-op if node already queued."""
        self._enqueue(node, datetime.now(UTC).timestamp())

    @override
    def schedule_at(self, node: DerivedNode, dt: datetime) -> None:
        """Schedule for processing at a specific wall-clock time. No-op if already queued."""
        self._enqueue(node, dt.timestamp())

    @override
    def after_refresh(self, node: DerivedNode) -> None:
        """Called after a node completes refresh — delegates to callback if set."""
        if self._after_refresh_callback is not None:
            self._after_refresh_callback(node)

    @override
    async def process_pending(self) -> int:
        """Process all past-due events (or all events when ``_respect_time`` is False).

        Returns the number processed and resets the since-flush counter
        (the conftest flush_all() no-op guard reads it).
        """
        self._ensure_primitives()
        queue, wakeup = self._queue, self._wakeup
        assert queue is not None and wakeup is not None
        drained = 0
        now_ts = datetime.now(UTC).timestamp() if self._respect_time else float("inf")
        while not queue.empty():
            try:
                event = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if event.scheduled_at > now_ts:
                await queue.put(event)
                break
            self._scheduled.pop(event.node_id, None)
            await event.node.refresh()
            drained += 1
        self._enqueued_since_flush = 0
        return drained

    async def _background_loop(self) -> None:
        self._ensure_primitives()
        queue, wakeup = self._queue, self._wakeup
        assert queue is not None and wakeup is not None
        while True:
            event = await queue.get()
            now_ts = datetime.now(UTC).timestamp()
            delay = event.scheduled_at - now_ts

            if not self._respect_time or delay <= 0:
                self._scheduled.pop(event.node_id, None)
                try:
                    await event.node.refresh()
                # lucidlint: ignore broad-except loop boundary — one node failure must not kill the processor
                except Exception as exc:
                    # One node's failure must not kill the background loop —
                    # log it and move on to the next event.
                    logger.exception("DAG processor failed for %s: %s", event.node_id, exc)
                    await asyncio.sleep(0)
                    continue
            else:
                await queue.put(event)
                wakeup.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(wakeup.wait(), timeout=delay)
            await asyncio.sleep(0)


# Production default — ONE scheduler for every thread: the event loop,
# the TestClient portal, and the processor thread must all resolve the
# same instance, or endpoints would schedule into a queue nobody drains.
# The override is test injection only (isolation fixtures); it is a plain
# module global because a ContextVar does not cross the portal thread.
_default_scheduler: RefreshScheduler = AsyncQueueScheduler()
_override_scheduler: RefreshScheduler | None = None


def get_scheduler() -> RefreshScheduler:
    return _override_scheduler if _override_scheduler is not None else _default_scheduler


# lucidlint: ignore unused-setter test-injection API — isolation_fixtures and dag tests call set_scheduler()
def set_scheduler(scheduler: RefreshScheduler) -> None:
    """Override the scheduler (used by tests to inject an isolated one)."""
    global _override_scheduler
    _override_scheduler = scheduler


# lucidlint: ignore unused test seam — clears the override in isolation fixtures across dag tests
def reset_scheduler() -> None:
    """Reset to default (clears the override)."""
    global _override_scheduler
    _override_scheduler = None


# ── Module-level convenience aliases (delegating to current scheduler) ──


async def flush_processor() -> int:
    """Process all currently scheduled nodes on the current executor.

    Production request paths reach the queue only via
    ``run_on_processor(flush_processor)`` — asyncio primitives are bound
    to whichever loop owns them.
    """
    return await get_scheduler().process_pending()


def set_after_refresh(callback: Callable[[DerivedNode], object]) -> None:
    sched = get_scheduler()
    sched._after_refresh_callback = callback


# ── The DAG processor thread ─────────────────────────────────────────
# ONE queue, ONE processor. The event loop never runs cascade work; the
# processor owns recompute AND persistence (blocking sqlite3 is harmless
# here — that is the point). See .kilo/plans/dag-save-queue.md.

# lucidlint: ignore global-state bounded module cache/state — single processor thread, deliberate
_processor_thread: threading.Thread | None = None
_processor_loop: asyncio.AbstractEventLoop | None = None
_processor_sched: AsyncQueueScheduler | None = None
_processor_task: asyncio.Task | None = None


def current_processor_thread() -> threading.Thread | None:
    """The processor thread once running; None in tests, lifespan-less
    scripts and startup — single-threaded contexts where mutation is safe
    by definition."""
    return _processor_thread


def assert_mutation_allowed() -> None:
    """Guard: DAG state mutates only on the processor thread.

    Fires only when the processor EXISTS and the caller is not it — the
    one dangerous case. Startup, scripts and tests never start a
    processor, so they are exempt by construction.
    """
    if _processor_thread is not None and threading.current_thread() is not _processor_thread:
        raise RuntimeError(
            "DAG mutation off the processor thread — enqueue work instead "
            "(await run_on_processor(...) from request handlers). See "
            "docs/dag-library.md → 'Thread rules'."
        )


def start_processor() -> None:
    """Start the DAG processor on its own thread (private event loop).

    No-op under ``dag.persistence.testing`` (tests drive the pipeline
    synchronously) and when already running.
    """
    global _processor_thread, _processor_loop, _processor_sched
    from dag.persistence import testing as _testing

    if _testing:
        logger.debug("DAG processor not started (testing mode)")
        return
    if _processor_thread is not None and _processor_thread.is_alive():
        return
    sched = get_scheduler()
    assert isinstance(sched, AsyncQueueScheduler)
    _processor_sched = sched
    _processor_loop = asyncio.new_event_loop()

    def _run() -> None:
        asyncio.set_event_loop(_processor_loop)

        async def _main() -> None:
            # Created INSIDE the running loop — a bare loop.create_task()
            # before run_forever() has no running loop and kills the thread.
            global _processor_task
            _processor_task = asyncio.create_task(_processor_sched._background_loop())
            with contextlib.suppress(asyncio.CancelledError):
                await _processor_task

        _processor_loop.run_until_complete(_main())
        _processor_loop.close()

    _processor_thread = threading.Thread(target=_run, name="dag-processor", daemon=True)
    _processor_thread.start()
    logger.info("DAG processor thread started (%s)", _processor_thread.name)


def _cancel_background_task() -> None:
    if _processor_task is not None and not _processor_task.done():
        _processor_task.cancel()


def stop_processor(timeout: float = 10.0) -> int:
    """Drain past-due work through the processor, stop its loop, join it.

    Returns the count of future-scheduled items (retries) abandoned;
    caller logs it — a wedged drain must not hang ``systemctl stop``.
    """
    global _processor_thread, _processor_loop, _processor_sched
    if _processor_loop is None or _processor_thread is None:
        return 0
    sched = _processor_sched
    assert isinstance(sched, AsyncQueueScheduler)

    async def _drain() -> int:
        while True:
            await sched.process_pending()
            if sched.queue_depth == 0:
                break
        return len(sched._scheduled)

    abandoned = 0
    try:
        abandoned = asyncio.run_coroutine_threadsafe(_drain(), _processor_loop).result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.error("processor drain did not finish within %ss", timeout)
    _processor_loop.call_soon_threadsafe(_cancel_background_task)
    _processor_thread.join(timeout=timeout)
    if _processor_thread.is_alive():
        logger.error("processor thread did not stop within %ss", timeout)
    _processor_thread = None
    _processor_loop = None
    _processor_sched = None
    _processor_task = None
    return abandoned


async def run_on_processor(fn: Callable[[], Any]) -> Any:
    """Run ``fn()`` on the processor thread and await its completion.

    Request handlers mutate DAG state ONLY through this — direct node
    mutation off the processor thread raises (see
    ``assert_mutation_allowed``). Single-threaded contexts (tests,
    startup, the processor itself) run inline.
    """
    if _processor_loop is None or threading.current_thread() is _processor_thread:
        result = fn()
        if inspect.isawaitable(result):
            result = await result
        return result
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(_run_maybe_async(fn), _processor_loop))


async def _run_maybe_async(fn: Callable[[], Any]) -> Any:
    result = fn()
    if inspect.isawaitable(result):
        result = await result
    return result
