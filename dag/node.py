from __future__ import annotations

import time
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
        self._computed_at: float = 0.0
        self._persisted_at: float = 0.0
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
                attempt = Attempt.pending()
            else:
                attempt = Attempt.impossible(stored.get("error", "unknown"))
            self._db_created_at = stored.get("_persisted_at", "")
            dep_ts = stored.get("dep_timestamps")
            self._loaded_dep_timestamps = dep_ts if isinstance(dep_ts, dict) else {}
            self._computed_at = time.monotonic()
            self._persisted_at = time.monotonic()
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
        if attempt.impossible:
            result["error"] = attempt.error
        result["provenance"] = (await self.build_provenance()).to_dict()
        return result

    def _persist(self, result_dict: dict,
                 dep_timestamps: dict[str, str] | None = None) -> None:
        from dag.persistence import save_node_result

        save_node_result(self._id, result_dict, dep_timestamps)
        self._persisted_at = time.monotonic()
        self._db_created_at = datetime.now(UTC).isoformat()

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
