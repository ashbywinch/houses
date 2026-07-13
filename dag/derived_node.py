from __future__ import annotations

from datetime import UTC, datetime
import asyncio
from inspect import iscoroutine
from abc import abstractmethod
from collections.abc import Callable

from dag.attempt import Attempt, Provenance
from dag.node import Node
from dag.signals import Slot
from typing import Generic, TypeVar

T = TypeVar("T")
_after_refresh: Callable[[DerivedNode], object] | None = None
_stale_queue: asyncio.Queue[DerivedNode] = None  # type: ignore[assignment]


def set_after_refresh(callback: Callable[[DerivedNode], object]) -> None:
    """Register a callback called after each node refresh (for broadcasting)."""
    global _after_refresh
    _after_refresh = callback


def _ensure_queue() -> None:
    global _stale_queue
    if _stale_queue is None:
        _stale_queue = asyncio.Queue()


async def flush_processor() -> None:
    """Synchronously drain the stale queue — used in tests after pushing
    source values, before reading derived nodes."""
    _ensure_queue()
    while not _stale_queue.empty():
        try:
            node = _stale_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        await node.refresh()


async def _processor() -> None:
    """Single consumer: pop stale nodes from the queue and refresh them."""
    _ensure_queue()
    while True:
        node = await _stale_queue.get()
        try:
            await node.refresh()
            if _after_refresh is not None:
                _after_refresh(node)
        except Exception:
            pass

class DerivedNode(Node[T], Generic[T]):
    """A node whose value is computed from other nodes.

    Subclasses declare their dependencies and implement ``compute()``.
    Results are cached until a dep's timestamp indicates a newer value.
    After each recompute, the result is persisted to SQLite.
    """

    def __init__(self, node_id: str, value_type: type[T],
                 deps: tuple[Node, ...]) -> None:
        super().__init__(node_id, value_type)
        self._deps = deps
        self._cached: Attempt[T] | None = None
        self._slots: list[Slot] = []
        _ensure_queue()

        loaded = self._load_attempt_from_db()
        if loaded is not None:
            self._cached = loaded

        for dep in deps:
            slot = Slot(self._on_dep_changed)
            self._slots.append(slot)
            dep.changed.connect(slot)

    def _on_dep_changed(self) -> None:
        """A dependency changed — push this node to the stale queue
        so the processor recomputes it."""
        _stale_queue.put_nowait(self)
        self.changed.emit()

    def _is_stale(self) -> bool:
        if self._cached is None:
            return True
        for dep in self._deps:
            if dep._persisted_at is not None and self._computed_at is not None:
                if dep._persisted_at > self._computed_at:
                    return True
            if self._loaded_dep_timestamps:
                stored = self._loaded_dep_timestamps.get(dep._id, "")
                if stored and dep._db_created_at != stored:
                    return True
        return False

    async def attempt(self) -> Attempt[T]:
        """Return the cached value.

        On first call (no cached value) computes synchronously — no
        concurrent writer exists yet.  After that, reads are instant
        unless stale (dep changed), in which case refresh() runs here.
        The processor handles background refreshes for nodes that
        aren't explicitly read.
        """
        if self._cached is not None:
            if self._is_stale():
                await self.refresh()
            return self._cached
        # First compute (no concurrent writer possible)
        await self.refresh()
        if self._cached is not None:
            return self._cached
        return Attempt.pending()

    async def refresh(self) -> None:
        """Recompute if stale, persist, and emit changed."""
        if not self._is_stale():
            return
        dep_attempts = [await dep.attempt() for dep in self._deps]
        if any(a.pending for a in dep_attempts):
            return
        try:
            result = self.compute(*dep_attempts)
            if iscoroutine(result):
                result = await result
        except Exception as e:
            result = Attempt.impossible(f"{self._id}: {e}")
        self._cached = result
        self._computed_at = datetime.now(UTC)
        dep_timestamps = {
            dep._id: dep._db_created_at for dep in self._deps
        }
        try:
            result_dict = await self.to_json()
        except Exception as e:
            result_dict = {
                "status": "impossible",
                "value": None,
                "error": str(e),
                "provenance": {"label": ""},
            }
        self._persist(result_dict, dep_timestamps)
        self.changed.emit()

    async def build_provenance(self) -> Provenance:
        sources: dict[str, Provenance] = {}
        for dep in self._deps:
            sources[dep._id] = await dep.build_provenance()
        return Provenance.composite(self._id, sources)


    async def to_json(self) -> dict:
        result = await super().to_json()
        if self._cached is not None:
            result["stale"] = self._is_stale()
        return result

    @abstractmethod
    def compute(self, *dep_attempts: Attempt) -> Attempt[T]:
        ...
