from __future__ import annotations

from typing import Generic, TypeVar

from dag.attempt import Attempt, Provenance
from dag.node import Node

T = TypeVar("T")


class SourceNode(Node[T], Generic[T]):
    """A leaf node whose value is set externally by enrichment modules,
    sheet imports, or user edits.

    Call ``.push(value, provenance)`` to set a new value. This emits the
    ``changed`` signal so that downstream ComputedNodes re-compute.
    """

    def __init__(self, node_id: str, value_type: type[T]) -> None:
        super().__init__(node_id, value_type)
        self._value: T | None = None
        self._provenance: Provenance = Provenance()

    def push(self, value: T, provenance: Provenance) -> None:
        """Store a new value and notify downstream nodes."""
        self._value = value
        self._provenance = provenance
        self.changed.emit()

    def attempt(self) -> Attempt[T]:
        if self._value is not None:
            return Attempt.succeeded(self._value, self._provenance)
        return Attempt.impossible("not set")
