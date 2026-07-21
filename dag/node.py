from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import TypeAdapter

from dag.attempt import Attempt, Provenance
from dag.signals import Signal

T = TypeVar("T")


def _humanify(name: str) -> str:
    """Convert a code-style identifier to a human-friendly label.
    'computed_transit' -> 'Computed Transit'
    """
    return " ".join(w.capitalize() for w in name.replace("-", " ").split("_"))


# Cache TypeAdapters by value type to avoid OOM from creating thousands
# of Pydantic schemas at startup (~19s, 5GB for 6000 nodes).
_adapter_cache: dict[type, TypeAdapter] = {}


def _get_adapter(t: type) -> TypeAdapter:
    if t not in _adapter_cache:
        _adapter_cache[t] = TypeAdapter(t)
    return _adapter_cache[t]


class Node(ABC, Generic[T]):
    """Base class for all DAG nodes."""

    def __init__(self, node_id: str, value_type: type[T], source_url: str = "") -> None:
        self._id = node_id
        self._value_type = value_type
        self._source_url = source_url
        self._adapter = _get_adapter(value_type)
        self.changed = Signal()
        raw = node_id.rstrip("/").split("/")[-1]
        self.display_name: str = _humanify(raw)
        self._computed_at: datetime | None = None
        self._persisted_at: datetime | None = None
        self._db_created_at: str = ""
        self._loaded_dep_timestamps: dict[str, str] = {}
        self._retry_at: datetime | None = None
        self._retry_count: int = 0
        self._max_retries: int = 3

    def _load_attempt_from_db(self) -> Attempt[T] | None:
        from dag.persistence import latest_node_result

        stored = latest_node_result(self._id)
        if stored is None:
            return None

        # Unconditionally restore the DB timestamp before any early return.
        # This ensures downstream nodes' _is_stale() dep-timestamp checks
        # don't spuriously fire when we can't deserialize the value.
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

        status = stored.get("status", "")
        if status == "succeeded":
            try:
                val = self._adapter.validate_python(stored["value"])
            except Exception:
                # Value doesn't match the current type (e.g. float persisted
                # before a str→Money migration).  Discard and recompute.
                return None
            return Attempt.succeeded(val)
        if status == "pending":
            retry_at = stored.get("retry_at")
            if retry_at:
                self._retry_at = datetime.fromisoformat(retry_at)
                self._retry_count = stored.get("retry_count", 0)
            return None

        return Attempt.impossible(stored.get("error", "unknown"))

    def latest_attempt(self) -> Attempt:
        """Synchronous access to the last known attempt.
        Override in subclasses that cache the attempt object."""
        raise RuntimeError(f"{type(self).__name__} does not support sync attempt access")

    @abstractmethod
    async def attempt(self) -> Attempt[T]:
        """Compute or retrieve the current value."""
        ...

    @abstractmethod
    async def build_provenance(self) -> Provenance:
        """Build provenance by walking dependency nodes.
        Subclasses override this to return a Provenance describing
        how this node's value was derived."""
        ...

    async def to_json(self) -> dict:
        attempt = await self.attempt()
        result: dict = {
            "status": attempt.status,
            "value": self._adapter.dump_python(attempt.value, mode="json") if attempt.succeeded else None,
        }
        result["succeeded"] = attempt.succeeded
        result["pending"] = attempt.pending
        result["impossible"] = attempt.impossible
        if attempt.impossible:
            result["error"] = attempt.error
        if self._source_url:
            result["source_url"] = self._source_url
        if not attempt.pending:
            result["provenance"] = (await self.build_provenance()).to_dict()
        return result

    async def to_json_value(self) -> dict:
        """Like to_json() but skips the expensive provenance tree build.

        Returns status, value, and metadata only — no ``provenance`` field.
        Use this for bulk-list endpoints where provenance is not needed.
        """
        attempt = await self.attempt()
        result: dict = {
            "status": attempt.status,
            "value": self._adapter.dump_python(attempt.value, mode="json") if attempt.succeeded else None,
        }
        result["succeeded"] = attempt.succeeded
        result["pending"] = attempt.pending
        result["impossible"] = attempt.impossible
        if attempt.impossible:
            result["error"] = attempt.error
        if self._source_url:
            result["source_url"] = self._source_url
        return result

    def _persist(self, result_dict: dict, dep_timestamps: dict[str, str] | None = None) -> None:
        from dag.persistence import save_node_result

        now_str = datetime.now(UTC).isoformat()
        save_node_result(self._id, result_dict, dep_timestamps, created_at=now_str)
        now = datetime.fromisoformat(now_str)
        self._persisted_at = now
        self._db_created_at = now_str
        if dep_timestamps is not None:
            self._loaded_dep_timestamps = dep_timestamps

    def _impossible(self, dep_attempts: dict[str, Attempt[T]], extra: str = "") -> Attempt[T]:
        parts = [self._id]
        if extra:
            parts.append(extra)
        for name, attempt in dep_attempts.items():
            if attempt is None:
                parts.append(f"{name}: not available")
            elif not attempt.succeeded:
                detail = attempt.error or "unknown"
                parts.append(f"{name}: {detail}")
        return Attempt.impossible("; ".join(parts))
