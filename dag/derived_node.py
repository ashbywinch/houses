from __future__ import annotations

from datetime import UTC, datetime
from abc import abstractmethod
from inspect import iscoroutine
from typing import Generic, TypeVar

from dag.attempt import Attempt, Provenance
from dag.node import Node
from dag.signals import Slot

T = TypeVar("T")


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

        loaded = self._load_attempt_from_db()
        if loaded is not None:
            self._cached = loaded

        for dep in deps:
            slot = Slot(self._on_dep_changed)
            self._slots.append(slot)
            dep.changed.connect(slot)

    def _on_dep_changed(self) -> None:
        """A dependency changed — recompute if we already have a cached value.

        During startup nodes have ``_cached is None`` and are computed
        lazily by the warmup / first read.  Once a node has computed at
        least once, a dep update triggers an eager recompute so the next
        HTTP response is fresh without a cold-start wait.
        """
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # no running loop — lazy compute on first read
        else:
            if self._cached is not None:
                asyncio.create_task(self.attempt())
        self.changed.emit()

    def _is_stale(self) -> bool:
        if self._cached is None:
            return True
        for dep in self._deps:
            if dep._persisted_at > self._computed_at:
                return True
            if self._loaded_dep_timestamps:
                stored = self._loaded_dep_timestamps.get(dep._id, "")
                if stored and dep._db_created_at != stored:
                    return True
        return False

    async def attempt(self) -> Attempt[T]:
        if self._is_stale():
            dep_attempts = [await dep.attempt() for dep in self._deps]
            # If any dep is pending, the result is pending — we don't have
            # enough information to compute yet.
            if any(a.pending for a in dep_attempts):
                self._cached = Attempt.pending()
                return self._cached
            try:
                result = self.compute(*dep_attempts)
                if iscoroutine(result):
                    result = await result
            except Exception as e:
                result = Attempt.impossible(f"{self._id}: {e}")
            self._cached = result
            self._computed_at = datetime.now(UTC).isoformat()
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
        return self._cached

    async def build_provenance(self) -> Provenance:
        sources: dict[str, Provenance] = {}
        for dep in self._deps:
            sources[dep._id] = await dep.build_provenance()
        return Provenance.composite(self._id, sources)

    @abstractmethod
    def compute(self, *dep_attempts: Attempt) -> Attempt[T]:
        ...
