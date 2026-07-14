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

_stale_queue: asyncio.Queue[DerivedNode] = None  # type: ignore[assignment]
_after_refresh: Callable[[DerivedNode], object] | None = None


def set_after_refresh(callback: Callable[[DerivedNode], object]) -> None:
    global _after_refresh
    _after_refresh = callback


def _ensure_queue() -> None:
    global _stale_queue
    if _stale_queue is None:
        _stale_queue = asyncio.Queue()


async def flush_processor() -> None:
    _ensure_queue()
    while not _stale_queue.empty():
        try:
            node = _stale_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        await node.refresh()


async def _processor() -> None:
    _ensure_queue()
    while True:
        node = await _stale_queue.get()
        try:
            await node.refresh()
            if _after_refresh is not None:
                _after_refresh(node)
        except Exception:
            pass
        await asyncio.sleep(0)


class DerivedNode(Node[T], Generic[T]):
    """A node whose value is computed from other nodes."""

    def __init__(self, node_id: str, value_type: type[T],
                 deps: tuple[Node, ...]) -> None:
        super().__init__(node_id, value_type)
        self._deps = deps
        self._attempt: Attempt[T] = Attempt.pending()
        self._slots: list[Slot] = []
        _ensure_queue()

        loaded = self._load_attempt_from_db()
        if loaded is not None and loaded.succeeded:
            self._attempt = loaded

        for dep in deps:
            slot = Slot(self._on_dep_changed)
            self._slots.append(slot)
            dep.changed.connect(slot)

        # If still pending after DB load, queue for recomputation.
        if self._attempt.pending:
            _stale_queue.put_nowait(self)

    def _on_dep_changed(self) -> None:
        if not self._is_stale():
            return
        _ensure_queue()
        _stale_queue.put_nowait(self)

    def _is_stale(self) -> bool:
        if self._attempt.pending:
            return True
        for dep in self._deps:
            if dep._persisted_at is not None and self._computed_at is not None:
                if dep._persisted_at > self._computed_at:
                    return True
            if isinstance(dep, DerivedNode):
                if dep._computed_at is not None and self._computed_at is not None:
                    if dep._computed_at > self._computed_at:
                        return True
            if self._loaded_dep_timestamps:
                stored = self._loaded_dep_timestamps.get(dep._id, "")
                if stored and dep._db_created_at != stored:
                    return True
        return False

    async def attempt(self) -> Attempt[T]:
        return self._attempt

    async def refresh(self) -> None:
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
        self._attempt = result
        self._computed_at = datetime.now(UTC)
        dep_timestamps = {dep._id: dep._db_created_at for dep in self._deps}
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
        for dep in self._deps:
            sources[dep._id] = await dep.build_provenance()
        return Provenance.composite(self._id, sources)

    async def to_json(self) -> dict:
        result = await super().to_json()
        if not self._attempt.pending:
            result["stale"] = self._is_stale()
        return result

    @abstractmethod
    def compute(self, *dep_attempts: Attempt) -> Attempt[T]:
        ...
