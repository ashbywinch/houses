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

    def __init__(self, respect_time: bool = True) -> None:
        self._queue: asyncio.PriorityQueue[QueueEvent] = asyncio.PriorityQueue()
        self._scheduled: dict[str, QueueEvent] = {}
        self._registered: dict[str, DerivedNode] = {}
        self._wakeup: asyncio.Event = asyncio.Event()
        self._after_refresh_callback: Callable[[DerivedNode], object] | None = None
        self._respect_time: bool = respect_time
        self._enqueued_since_flush: int = 0
        """Work items enqueued since the last complete drain — the conftest
        flush_all() no-op guard reads it."""
        self._requeue_counts: dict[str, int] = {}

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

    @override
    def _enqueue(self, node: DerivedNode, scheduled_at: float) -> None:
        """Queue the node unless already queued, then wake the processor.

        The queue and its wakeup event are asyncio primitives — they belong to
        the processor's loop and must only be touched there.  A caller on
        another thread (a request handler scheduling directly) hands the
        enqueue over instead: the primitive stays single-threaded by
        construction, rather than by every caller remembering.
        """
        if _processor_loop is not None and threading.current_thread() is not _processor_thread:
            _processor_loop.call_soon_threadsafe(self._enqueue, node, scheduled_at)
            return
        if node._id in self._scheduled:
            return
        event = QueueEvent(scheduled_at=scheduled_at, node_id=node._id, node=node)
        self._scheduled[node._id] = event
        self._enqueued_since_flush += 1
        self._queue.put_nowait(event)
        self._wakeup.set()

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
        drained = 0
        now_ts = datetime.now(UTC).timestamp() if self._respect_time else float("inf")
        while not self._queue.empty():
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if event.scheduled_at > now_ts:
                await self._queue.put(event)
                break
            self._scheduled.pop(event.node_id, None)
            await event.node.refresh()
            if event.node._attempt.pending:
                self._requeue_after_deps(event)
            else:
                self._requeue_counts.pop(event.node_id, None)
                drained += 1
        self._enqueued_since_flush = 0
        if self._queue.empty():
            # A complete drain must leave the gauge truthful: queue_depth
            # counts a set wakeup as one pending item, so a flag left over
            # from the last enqueue would report phantom work forever.
            self._wakeup.clear()
        return drained

    def _requeue_after_deps(self, event: QueueEvent) -> None:
        """Re-queue a deferred event directly after the last of its deps.

        A refresh that finds pending deps leaves the node pending and
        returns without computing. Dropping the event there strands the
        node pending forever — nothing re-schedules it. Put the event
        back just after the last of the node's queued deps instead: it
        retries the moment its inputs are ready, and never waits behind
        unrelated long-backoff events.

        A node may only defer once per not-yet-complete dep (the graph
        is acyclic), so re-queues per node are bounded. Exceeding the
        bound means a pending dep never enters the queue — a contract
        bug — and must fail loudly rather than spin the drain.
        """
        # lucidlint: ignore inline-import cycle break — DerivedNode imports get_scheduler from this module
        from dag.derived_node import DerivedNode
        active = [dep for dep in event.node._get_active_deps() if dep is not None]
        # Re-queue only when the deferral waits on queueable work: a
        # pending DerivedNode that is actually IN the queue (register
        # schedules fresh nodes; deferrals re-queue). A pending node
        # that is parked OUTSIDE the queue is dormant — it waits on an
        # unpushed user input, nothing will ever pop for it, and the
        # input's push signal already owns the wake-up for the whole
        # downstream chain. Re-queueing behind a parked dep would spin
        # the drain forever.
        if not any(
            isinstance(dep, DerivedNode)
            and dep._attempt.pending
            and dep._id in self._scheduled
            for dep in active
        ):
            return
        dep_ids = {dep._id for dep in active}
        now = datetime.now(UTC).timestamp()
        last_dep_at = max(
            (
                queued.scheduled_at
                for node_id, queued in self._scheduled.items()
                if node_id in dep_ids
            ),
            default=now,
        )
        queue = self._queue
        assert queue is not None
        requeued = QueueEvent(
            scheduled_at=last_dep_at + 1e-6, node_id=event.node_id, node=event.node
        )
        count = self._requeue_counts.get(event.node_id, 0) + 1
        if count > len(self._registered):
            self._requeue_counts.pop(event.node_id, None)
            raise RuntimeError(
                f"{event.node_id} re-queued {count} times in one drain: "
                "a pending dependency never enters the queue"
            )
        self._requeue_counts[event.node_id] = count
        self._scheduled[event.node_id] = requeued
        queue.put_nowait(requeued)
        if self._wakeup is not None:
            self._wakeup.set()

    async def _background_loop(self) -> None:
        while True:
            event = await self._queue.get()
            now_ts = datetime.now(UTC).timestamp()
            delay = event.scheduled_at - now_ts

            if not self._respect_time or delay <= 0:
                self._scheduled.pop(event.node_id, None)
                try:
                    await event.node.refresh()
                    if event.node._attempt.pending:
                        self._requeue_after_deps(event)
                    else:
                        self._requeue_counts.pop(event.node_id, None)
                # lucidlint: ignore broad-except loop boundary — one node failure must not kill the processor
                except Exception as exc:
                    # One node's failure must not kill the background loop —
                    # log it and move on to the next event.
                    logger.exception("DAG processor failed for %s: %s", event.node_id, exc)
                    await asyncio.sleep(0)
                    continue
            else:
                await self._queue.put(event)
                self._wakeup.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wakeup.wait(), timeout=delay)
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

_processor_thread: threading.Thread | None = None
_processor_loop: asyncio.AbstractEventLoop | None = None
_processor_sched: AsyncQueueScheduler | None = None
_processor_task: asyncio.Task | None = None



# lucidlint: ignore unused deliberate test seam asserted by the isolation fixture
# and the queue tests — production never needs to ask who the processor is.
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
    # lucidlint: ignore inline-import cycle break — persistence imports this module's scheduler at top
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
            with _submissions_lock:
                in_flight = _pending_submissions
            if sched._queue.empty() and in_flight == 0:
                break
            # A submission applies its write and enqueues its cascade; give it
            # a slice before deciding the processor is idle.  Not sleep(0):
            # that spins the loop at full tilt while a submission runs.
            await asyncio.sleep(_DRAIN_YIELD_S)
        return len(sched._scheduled)

    abandoned = 0
    try:
        abandoned = asyncio.run_coroutine_threadsafe(_drain(), _processor_loop).result(timeout=timeout)
    # lucidlint: ignore swallow logged above — a wedged drain must not hang systemctl stop
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


#: Queued submissions not yet applied.  ``stop_processor`` drains the
#: scheduler queue AND this count: a submission in flight (or one whose
#: cascade has not been enqueued yet) must land before the process exits,
#: or a write the user already saw confirmed would vanish.
#: The yield granted to an in-flight submission between drain slices —
#: a responsive idle decision without a busy spin.
_DRAIN_YIELD_S = 0.01

_pending_submissions: int = 0
_submissions_lock = threading.Lock()


def submit_to_processor(fn: Callable[[], Any]) -> None:
    """Hand ``fn()`` to the processor thread and RETURN — the caller does not wait.

    This is how work gets onto the queue: a producer states what happened
    ("this node now holds this value"), the processor applies it (persist,
    notify, cascade).  A synchronous caller cannot await a coroutine on
    another thread's loop, so the handover has to look like this — but it is
    a handover, not a rendezvous: nothing is executed in the caller's turn.

    Single-threaded contexts (tests, startup, scripts, the processor itself)
    have no processor thread, so the work runs inline — the same convention
    ``run_on_processor`` uses, which is what keeps the suite synchronous.

    A failure inside ``fn`` is logged loudly: the producer is long gone by
    then, and a silent failure would mean the write never landed.
    """
    if _processor_loop is None or threading.current_thread() is _processor_thread:
        _run_and_log(fn)
        return
    global _pending_submissions
    with _submissions_lock:
        _pending_submissions += 1
    future = asyncio.run_coroutine_threadsafe(_run_maybe_async(fn), _processor_loop)
    future.add_done_callback(_submission_finished)


def _run_and_log(fn: Callable[[], Any]) -> None:
    try:
        result = fn()
        if inspect.iscoroutine(result):
            # A single-threaded context has no loop to run it on: queued work
            # must be synchronous here (the processor path awaits coroutines).
            result.close()
            raise RuntimeError("queued work must be synchronous without a processor thread")
    
    # lucidlint: ignore swallow the producer has already returned — the failing work is logged for the
    # operator; a raise here would take down the caller long after the fact
    except Exception:
        logger.exception("processor work failed (production of a queued write)")


def _submission_finished(future: concurrent.futures.Future) -> None:
    """The submitted work is applied (or failed): stop counting it."""
    global _pending_submissions
    with _submissions_lock:
        _pending_submissions -= 1
    _log_processor_failure(future)


def _log_processor_failure(future: concurrent.futures.Future) -> None:

    try:
        future.result()
    # lucidlint: ignore swallow the callback is the failure's ONLY surface — logging it IS the delivery
    except Exception:
        logger.exception("queued processor work failed after the producer returned")


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
