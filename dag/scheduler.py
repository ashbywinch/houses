"""Pluggable refresh scheduler for DerivedNodes.

Production: ``AsyncQueueScheduler`` — background ``asyncio.PriorityQueue``.
Tests: inject an isolated instance via ``set_scheduler()``.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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

    def register(self, node: DerivedNode) -> None:
        """Called when a DerivedNode is created."""

    def unregister(self, node: DerivedNode) -> None:
        """Called when a DerivedNode is disconnected (cleanup)."""

    def schedule(self, node: DerivedNode) -> None:
        """Request that *node* be refreshed when convenient."""

    def schedule_at(self, node: DerivedNode, dt: datetime) -> None:
        """Schedule *node* for refresh at wall-clock time *dt*."""

    async def process_pending(self) -> None:
        """Synchronously process all currently scheduled nodes."""

    def after_refresh(self, node: DerivedNode) -> None:
        """Called after a node completes refresh (no-op default)."""


class AsyncQueueScheduler(RefreshScheduler):
    """Production scheduler — background ``asyncio.PriorityQueue``.

    When *respect_time* is True (default), events scheduled in the future
    are deferred until their wall-clock time arrives.  When False (test mode),
    every event executes immediately on ``process_pending()``.
    """

    def __init__(self, respect_time: bool = True) -> None:
        self._queue: asyncio.PriorityQueue[QueueEvent] = asyncio.PriorityQueue()
        self._scheduled: dict[str, QueueEvent] = {}
        self._wakeup = asyncio.Event()
        self._after_refresh_callback: Callable[[DerivedNode], object] | None = None
        self._respect_time = respect_time

    def register(self, node: DerivedNode) -> None:
        """Called when a node is created. Schedules it at its stored retry time or immediately."""
        if node._id in self._scheduled:
            return
        if node._retry_at is not None and node._retry_at > datetime.now(UTC):
            self.schedule_at(node, node._retry_at)
        elif node._attempt.pending or node._is_stale():
            self.schedule(node)

    def unregister(self, node: DerivedNode) -> None:
        """Called when a node is disconnected (cleanup)."""
        self._scheduled.pop(node._id, None)

    def schedule(self, node: DerivedNode) -> None:
        """Schedule for immediate processing. No-op if node already queued."""
        if node._id in self._scheduled:
            return
        now_ts = datetime.now(UTC).timestamp()
        event = QueueEvent(scheduled_at=now_ts, node_id=node._id, node=node)
        self._scheduled[node._id] = event
        self._queue.put_nowait(event)
        self._wakeup.set()

    def schedule_at(self, node: DerivedNode, dt: datetime) -> None:
        """Schedule for processing at a specific wall-clock time. No-op if already queued."""
        if node._id in self._scheduled:
            return
        event = QueueEvent(scheduled_at=dt.timestamp(), node_id=node._id, node=node)
        self._scheduled[node._id] = event
        self._queue.put_nowait(event)
        self._wakeup.set()

    def after_refresh(self, node: DerivedNode) -> None:
        """Called after a node completes refresh — delegates to callback if set."""
        if self._after_refresh_callback is not None:
            self._after_refresh_callback(node)

    async def process_pending(self) -> None:
        """Process all past-due events (or all events when ``_respect_time`` is False)."""
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

    async def _background_loop(self) -> None:
        while True:
            event = await self._queue.get()
            now_ts = datetime.now(UTC).timestamp()
            delay = event.scheduled_at - now_ts

            if not self._respect_time or delay <= 0:
                self._scheduled.pop(event.node_id, None)
                try:
                    await event.node.refresh()
                except Exception as exc:
                    logger.exception("DAG processor failed for %s: %s", event.node_id, exc)
            else:
                await self._queue.put(event)
                self._wakeup.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wakeup.wait(), timeout=delay)
            await asyncio.sleep(0)


# Production default — shared across all asyncio tasks.
# ContextVar override is only for test injection (set_scheduler).
_default_scheduler: RefreshScheduler = AsyncQueueScheduler()
_scheduler_var: contextvars.ContextVar[RefreshScheduler | None] = contextvars.ContextVar("_dag_scheduler", default=None)


def _get_scheduler() -> RefreshScheduler:
    override = _scheduler_var.get()
    return override if override is not None else _default_scheduler


def set_scheduler(scheduler: RefreshScheduler) -> None:
    """Override the scheduler (used by tests to inject an isolated one)."""
    _scheduler_var.set(scheduler)


def reset_scheduler() -> None:
    """Reset to default (clears the ContextVar override)."""
    _scheduler_var.set(None)


# ── Module-level convenience aliases (delegating to current scheduler) ──


async def flush_processor() -> None:
    await _get_scheduler().process_pending()


def start_processor() -> asyncio.Task:
    """Start the background refresh loop. Returns the asyncio Task."""
    sched = _get_scheduler()
    if isinstance(sched, AsyncQueueScheduler):
        return asyncio.create_task(sched._background_loop())
    raise TypeError(f"Cannot start background processor on {type(sched).__name__}")


def set_after_refresh(callback: Callable[[DerivedNode], object]) -> None:
    sched = _get_scheduler()
    sched._after_refresh_callback = callback
