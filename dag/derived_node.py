from __future__ import annotations

import asyncio
import contextvars
import logging
from abc import abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from inspect import iscoroutine
from typing import Generic, TypeVar

from dag.attempt import Attempt, Provenance
from dag.node import Node
from dag.signals import Connection, Slot

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── Refresh scheduler (DI via ContextVar) ────────────────────────────

class RefreshScheduler:
    """Pluggable scheduler for refreshing stale DerivedNodes.

    Production: ``AsyncQueueScheduler`` — background ``asyncio.Queue``.
    Tests: provide an isolated instance with ``set_scheduler()``.
    """
    def register(self, node: DerivedNode) -> None:
        """Called when a DerivedNode is created."""

    def unregister(self, node: DerivedNode) -> None:
        """Called when a DerivedNode is disconnected (cleanup)."""

    def schedule(self, node: DerivedNode) -> None:
        """Request that *node* be refreshed when convenient."""

    async def process_pending(self) -> None:
        """Synchronously process all currently scheduled nodes."""


class AsyncQueueScheduler(RefreshScheduler):
    """Default production scheduler — background ``asyncio.Queue``."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[DerivedNode] = asyncio.Queue()
        self._after_refresh: Callable[[DerivedNode], object] | None = None

    def register(self, node: DerivedNode) -> None:
        if node._attempt.pending or node._is_stale():
            self._queue.put_nowait(node)

    def unregister(self, node: DerivedNode) -> None:
        pass  # Can't remove from asyncio.Queue; stale refreshes are harmless

    def schedule(self, node: DerivedNode) -> None:
        self._queue.put_nowait(node)

    async def process_pending(self) -> None:
        while not self._queue.empty():
            try:
                node = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await node.refresh()

    async def _background_loop(self) -> None:
        while True:
            node = await self._queue.get()
            try:
                await node.refresh()
                if self._after_refresh is not None:
                    self._after_refresh(node)
            except Exception as exc:
                logger.exception("DAG processor failed for %s: %s", node._id, exc)
            await asyncio.sleep(0)


class _TestScheduler(RefreshScheduler):
    """Isolated scheduler for tests — no global state.

    Each test creates its own instance via ``set_scheduler()``.
    Call ``process_pending()`` to flush only this scheduler's nodes.
    """

    def __init__(self) -> None:
        self._pending: list[DerivedNode] = []

    def register(self, node: DerivedNode) -> None:
        if node._attempt.pending or node._is_stale():
            self._pending.append(node)

    def unregister(self, node: DerivedNode) -> None:
        self._pending = [n for n in self._pending if n is not node]

    def schedule(self, node: DerivedNode) -> None:
        self._pending.append(node)

    async def process_pending(self) -> None:
        while self._pending:
            todo, self._pending = self._pending, []
            for node in todo:
                await node.refresh()
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
        if loaded is not None and loaded.succeeded:
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
                self.changed.emit()
                return
        try:
            result = self.compute(*dep_attempts)
            if iscoroutine(result):
                result = await result
        except Exception as e:
            result = Attempt.impossible(f"{self._id}: {e}")
        self._attempt = result
        self._computed_at = datetime.now(UTC)
        dep_timestamps = {dep._id: dep._db_created_at for dep in active_deps}
        try:
            result_dict = await self.to_json()
        except Exception as e:
            result_dict = {
                "status": "impossible", "value": None, "error": str(e),
                "provenance": {"label": ""},
            }
        self._persist(result_dict, dep_timestamps)
        self.changed.emit()

    async def build_provenance(self) -> Provenance:
        sources: dict[str, Provenance] = {}
        for dep in self._get_active_deps():
            sources[dep._id] = await dep.build_provenance()
        description = self._attempt.error if self._attempt.impossible else ""
        return Provenance(label=self.display_name, description=description, sources=sources)

    async def to_json(self) -> dict:
        result = await super().to_json()
        if not self._attempt.pending:
            result["stale"] = self._is_stale()
        return result

    @abstractmethod
    def compute(self, *dep_attempts: Attempt) -> Attempt[T]:
        ...
