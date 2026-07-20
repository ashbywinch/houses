from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from inspect import iscoroutine
from typing import Generic, TypeVar

from dag.attempt import Attempt, Provenance
from dag.node import Node
from dag.signals import Connection, Slot

logger = logging.getLogger(__name__)

T = TypeVar("T")


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
        self._after_refresh: Callable[[DerivedNode], object] | None = None
        self._respect_time = respect_time

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

def set_after_refresh(callback: Callable[[DerivedNode], object]) -> None:
    sched = _get_scheduler()
    if isinstance(sched, AsyncQueueScheduler):
        sched._after_refresh = callback


# Production default — shared across all asyncio tasks.
# ContextVar override is only for test injection (set_scheduler).
_default_scheduler: RefreshScheduler = AsyncQueueScheduler()
_scheduler_var: contextvars.ContextVar[RefreshScheduler | None] = (
    contextvars.ContextVar("_dag_scheduler", default=None)
)


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


# ── DerivedNode ─────────────────────────────────────────────────────

class DerivedNode(Node[T], Generic[T]):
    """A node whose value is computed from other nodes."""

    def __init__(self, node_id: str, value_type: type[T],
                 deps: tuple[Node, ...]) -> None:
        super().__init__(node_id, value_type)
        self._deps = deps
        self._attempt: Attempt[T] = Attempt.pending()
        self._connections: list[Connection] = []
        self._slots: list[Slot] = []

        loaded = self._load_attempt_from_db()
        if loaded is not None:
            self._attempt = loaded
        for dep in deps:
            slot = Slot(self._on_dep_changed)
            self._slots.append(slot)
            conn = dep.changed.connect(slot)
            self._connections.append(conn)

        _get_scheduler().register(self)

    def disconnect(self) -> None:
        """Disconnect all signal connections and unregister from the scheduler."""
        for conn in self._connections:
            conn.disconnect()
        self._connections.clear()
        _get_scheduler().unregister(self)
    def _get_active_deps(self) -> tuple[Node, ...]:
        return self._deps

    def latest_attempt(self) -> Attempt:
        return self._attempt

    def _on_dep_changed(self) -> None:
        if not self._is_stale():
            return
        _get_scheduler().schedule(self)

    def _is_stale(self) -> bool:
        if self._retry_at is not None:
            return True
        if self._attempt.pending:
            return True
        for dep in self._get_active_deps():
            if dep._persisted_at is not None and self._computed_at is not None \
                    and dep._persisted_at > self._computed_at:
                return True
            if isinstance(dep, DerivedNode) and dep._computed_at is not None \
                    and self._computed_at is not None \
                    and dep._computed_at > self._computed_at:
                return True
            if self._loaded_dep_timestamps:
                stored = self._loaded_dep_timestamps.get(dep._id, "")
                if stored and dep._db_created_at != stored:
                    return True
        return False

    async def attempt(self) -> Attempt[T]:
        return self._attempt

    @property
    def _skip_impossible_dep_check(self) -> bool:
        """Override in subclasses whose compute() handles failed deps gracefully.

        When True, the generic impossible-dep short-circuit in refresh() is
        skipped, allowing compute() to receive impossible dep attempts and
        handle them (e.g., IfThenElseNode falls back to else branch,
        CommuteSelectorNode falls back to bus when transit fails).
        """
        return False

    def _is_transient_error(self, exc: Exception) -> bool:
        """Override in subclasses to identify retryable errors.

        When True, ``refresh()`` calls ``schedule_retry()`` and returns
        ``Attempt.pending()`` instead of ``Attempt.impossible()``.
        The default returns False (no retry for any error).
        """
        return False
    def schedule_retry(self, delay: timedelta) -> bool:
        """Schedule a DAG-level retry at now + delay.

        Returns True if the retry was scheduled, False if max retries exceeded.
        When False, the caller should return ``Attempt.impossible`` instead of
        ``Attempt.pending()`` so the node doesn't stay pending forever.
        """
        if self._retry_count >= self._max_retries:
            return False
        self._retry_at = datetime.now(UTC) + delay
        _get_scheduler().schedule_at(self, self._retry_at)
        return True
    def _retry_delay_from(self, exc: Exception, base_delay: timedelta = timedelta(seconds=10)) -> timedelta:
        """Extract retry delay from an exception, or use exponential backoff."""
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            return timedelta(seconds=min(retry_after, 300))
        delay_sec = base_delay.total_seconds() * (2 ** self._retry_count)
        if self._retry_count < self._max_retries:
            self._retry_count += 1
        return timedelta(seconds=min(delay_sec, 300))

    async def refresh(self) -> None:
        if not self._is_stale():
            return
        active_deps = self._get_active_deps()
        dep_attempts = [await dep.attempt() for dep in active_deps]
        if any(a.pending for a in dep_attempts):
            return
        if not self._skip_impossible_dep_check:
            impossible_deps = [a for a in dep_attempts if a.impossible]
            if impossible_deps:
                errors = "; ".join(a.error or "unknown" for a in impossible_deps)
                self._attempt = Attempt.impossible(f"dep failed: {errors}")
                self._computed_at = datetime.now(UTC)
                self._retry_at = None  # dep is permanently gone, cancel retry
                dep_timestamps = {dep._id: dep._db_created_at for dep in active_deps}
                result_dict = {
                    "status": "impossible", "value": None,
                    "error": f"dep failed: {errors}",
                    "provenance": {"label": ""},
                }
                self._persist(result_dict, dep_timestamps)
                self.changed.emit()
                if _get_scheduler()._after_refresh is not None:
                    _get_scheduler()._after_refresh(self)
                return
        try:
            result = self.compute(*dep_attempts)
            if iscoroutine(result):
                result = await result
        except Exception as e:
            # Check if this is a transient error that should be retried.
            # Subclasses can override _is_transient_error for custom logic.
            if self._is_transient_error(e):
                if not self.schedule_retry(self._retry_delay_from(e)):
                    result = Attempt.impossible(f"{self._id}: retry exhausted ({e})")
                else:
                    result = Attempt.pending()
            else:
                result = Attempt.impossible(f"{self._id}: {e}")

        self._attempt = result
        self._computed_at = datetime.now(UTC)

        dep_timestamps = {dep._id: dep._db_created_at for dep in active_deps}

        if result.pending:
            try:
                result_dict = await self.to_json()
            except Exception as e:
                result_dict = {
                    "status": "pending", "value": None, "error": str(e),
                    "provenance": {"label": ""},
                }
            self._persist(result_dict, dep_timestamps)
            return

        self._retry_at = None
        self._retry_count = 0 if result.succeeded else self._retry_count

        try:
            result_dict = await self.to_json()
        except Exception as e:
            result_dict = {
                "status": "impossible", "value": None, "error": str(e),
                "provenance": {"label": ""},
            }
        self._persist(result_dict, dep_timestamps)
        self.changed.emit()
        if _get_scheduler()._after_refresh is not None:
            _get_scheduler()._after_refresh(self)
    async def build_provenance(self) -> Provenance:
        sources: dict[str, Provenance] = {}
        for dep in self._get_active_deps():
            sources[dep._id] = await dep.build_provenance()
        description = self._attempt.error if self._attempt.impossible else ""
        return Provenance(label=self.display_name, description=description, sources=sources)

    async def to_json(self) -> dict:
        result = await super().to_json()
        if self._retry_at is not None:
            result["retry_at"] = self._retry_at.isoformat()
            result["retry_count"] = self._retry_count
        if not self._attempt.pending:
            result["stale"] = self._is_stale()
        return result

    @abstractmethod
    def compute(self, *dep_attempts: Attempt) -> Attempt[T]:
        ...
