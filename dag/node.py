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
            succeeded = stored["succeeded"]
            if succeeded:
                val = self._adapter.validate_python(stored["value"])
                prov = Provenance(
                    stored.get("provenance", {}).get("label", ""),
                    stored.get("provenance", {}).get("description", ""),
                )
                attempt: Attempt[T] = Attempt.succeeded(val, prov)
            else:
                attempt = Attempt.impossible(stored.get("error", "unknown"))
            self._db_created_at = stored.get("_persisted_at", "")
            dep_ts = stored.get("dep_timestamps")
            self._loaded_dep_timestamps = dep_ts if isinstance(dep_ts, dict) else {}
            self._computed_at = time.monotonic()
            self._persisted_at = time.monotonic()
            return attempt
        return None

    @property
    def id(self) -> str:
        return self._id

    @property
    def value_type(self) -> type[T]:
        return self._value_type

    @abstractmethod
    async def attempt(self) -> Attempt[T]:
        ...

    async def to_json(self) -> dict:
        attempt = await self.attempt()
        result: dict[str, Any] = {
            "succeeded": attempt.is_succeeded,
            "provenance": self._provenance_to_json(attempt.provenance),
        }
        if attempt.is_succeeded:
            result["value"] = self._adapter.dump_python(attempt.value_or_none())
            result["error"] = None
        else:
            result["value"] = None
            result["error"] = attempt._error
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
            if not attempt.is_succeeded:
                detail = attempt._error or "unknown"
                parts.append(f"{name}: {detail}")
        return Attempt.impossible("; ".join(parts))

    def _provenance_to_json(self, prov: Provenance) -> dict:
        result: dict[str, Any] = {"label": prov.label}
        if prov.description:
            result["description"] = prov.description
        if prov.source_attempts:
            result["sources"] = {
                name: self._provenance_to_json(a.provenance)
                for name, a in prov.source_attempts.items()
            }
        return result
