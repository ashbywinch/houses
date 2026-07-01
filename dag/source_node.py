from __future__ import annotations

from typing import Any, Generic, TypeVar

from dag.attempt import Attempt, Provenance
from dag.node import Node

T = TypeVar("T")


class SourceNode(Node[T], Generic[T]):
    """A leaf node whose value is set externally by enrichment modules,
    sheet imports, or user edits.

    Call ``.push(value, source_label)`` to set a new value. This emits the
    ``changed`` signal so that downstream ComputedNodes re-compute.
    Persists to SQLite automatically on every push.
    """

    def __init__(self, node_id: str, value_type: type[T]) -> None:
        super().__init__(node_id, value_type)
        self._value: T | None = None
        self._source_label: str = ""
        loaded = self._load_attempt_from_db()
        if loaded is not None and loaded.succeeded:
            self._value = loaded.value_or_none()
            self._source_label = self._load_persisted_label()

    def _load_persisted_label(self) -> str:
        """Read the source_label from the last persisted result."""
        from dag.persistence import latest_node_result

        stored = latest_node_result(self._id)
        if stored is not None:
            return stored.get("source_label", "")
        return ""

    def push(self, value: T, source_label: str = "") -> None:
        """Set a new value and persist.

        Args:
            value: The value to store.
            source_label: Human-readable source identifier
                (e.g. ``"Rightmove"``, ``"User correction"``, ``"TfL API"``).
        """
        self._value = value
        self._source_label = source_label
        result_dict: dict[str, Any] = {
            "status": "succeeded",
            "value": self._adapter.dump_python(value),
            "source_label": source_label,
        }
        self._persist(result_dict)
        self.changed.emit()

    async def attempt(self) -> Attempt[T]:
        if self._value is not None:
            return Attempt.succeeded(self._value)
        return Attempt.pending()

    async def build_provenance(self) -> Provenance:
        return Provenance.from_label(self._source_label)
