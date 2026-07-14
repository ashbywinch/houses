from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import TypeAdapter

from dag.attempt import Attempt, Provenance
from dag.signals import Signal

T = TypeVar("T")


class Node(ABC, Generic[T]):
    """Base class for all DAG nodes."""

    def __init__(self, node_id: str, value_type: type[T]) -> None:
        self._id = node_id
        self._value_type = value_type
        self._adapter = TypeAdapter(value_type)
        self.changed = Signal()
        self._computed_at: datetime | None = None
        self._persisted_at: datetime | None = None
        self._db_created_at: str = ""
        self._loaded_dep_timestamps: dict[str, str] = {}

    def _load_attempt_from_db(self) -> Attempt[T] | None:
        from dag.persistence import latest_node_result

        stored = latest_node_result(self._id)
        if stored is not None:
            status = stored.get("status", "")
            if status == "succeeded":
                val = self._adapter.validate_python(stored["value"])
                attempt: Attempt[T] = Attempt.succeeded(val)
            elif status == "pending":
                # A pending result means computation never finished — treat as
                # not cached so the processor can retry.
                return None
            else:
                attempt = Attempt.impossible(stored.get("error", "unknown"))
            # Parse ISO timestamps from DB into proper datetime objects
            persisted = stored.get("_persisted_at", "")
            if persisted:
                try:
                    self._persisted_at = datetime.fromisoformat(persisted)
                except (ValueError, TypeError):
                    self._persisted_at = None
            self._db_created_at = persisted
            self._computed_at = self._persisted_at
            dep_ts = stored.get("_dep_timestamps")
            if dep_ts:
                self._loaded_dep_timestamps = dep_ts
            return attempt
        return None

    @abstractmethod
    async def attempt(self) -> Attempt[T]:
        """Compute or retrieve the current value."""
        ...

    @abstractmethod
    async def build_provenance(self) -> Provenance:
        """Build provenance by walking dependency nodes.

        Returns a ``Provenance`` tree describing where this node's
        value came from.  The tree is computed dynamically — it is
        not cached on the ``Attempt`` object.
        """
        ...

    async def to_json(self) -> dict:
        attempt = await self.attempt()
        result: dict[str, Any] = {
            "status": attempt.status,
            "value": self._adapter.dump_python(attempt.value)
            if attempt.succeeded else None,
        }
        result["succeeded"] = attempt.succeeded
        result["pending"] = attempt.pending
        result["impossible"] = attempt.impossible
        result["stale"] = False
        if attempt.impossible:
            result["error"] = attempt.error
        result["provenance"] = (await self.build_provenance()).to_dict()
        return result

    def _persist(self, result_dict: dict,
                 dep_timestamps: dict[str, str] | None = None) -> None:
        from dag.persistence import save_node_result

        save_node_result(self._id, result_dict, dep_timestamps)
        now = datetime.now(UTC)
        self._persisted_at = now
        self._db_created_at = now.isoformat()
        # Keep stale-check state in sync so _is_stale() sees the new
        # dep timestamps immediately, preventing re-queue loops.
        if dep_timestamps is not None:
            self._loaded_dep_timestamps = dep_timestamps

    def _impossible(self, dep_attempts: dict[str, Attempt[T]],
                    extra: str = "") -> Attempt[T]:
        parts = [self._id]
        if extra:
            parts.append(extra)
        for name, attempt in dep_attempts.items():
            if not attempt.succeeded:
                detail = attempt.error or "unknown"
                parts.append(f"{name}: {detail}")
        return Attempt.impossible("; ".join(parts))
