from __future__ import annotations

from typing import Any, Generic, TypeVar

from dag.attempt import Attempt, Provenance
from dag.node import Node

T = TypeVar("T")


class SourceNode(Node[T], Generic[T]):
    """A leaf node whose value is set externally by enrichment modules,
    sheet imports, or user edits.

    Call ``.push(value, provenance)`` to set a new value. This emits the
    ``changed`` signal so that downstream ComputedNodes re-compute.
    Persists to SQLite automatically on every push.
    """

    def __init__(self, node_id: str, value_type: type[T]) -> None:
        super().__init__(node_id, value_type)
        self._value: T | None = None
        self._provenance: Provenance = Provenance()
        loaded = self._load_attempt_from_db()
        if loaded is not None and loaded.is_succeeded:
            self._value = loaded.value_or_none()
            self._provenance = loaded.provenance

    def push(self, value: T, provenance: Provenance) -> None:
        self._value = value
        self._provenance = provenance
        result_dict: dict[str, Any] = {
            "succeeded": True,
            "value": self._adapter.dump_python(value),
            "error": None,
            "provenance": {"label": provenance.label},
        }
        self._persist(result_dict)
        self.changed.emit()

    async def attempt(self) -> Attempt[T]:
        if self._value is not None:
            return Attempt.succeeded(self._value, self._provenance)
        return Attempt.impossible("not set")
