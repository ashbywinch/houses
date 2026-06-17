from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import TypeAdapter

from dag.attempt import Attempt, Provenance
from dag.signals import Signal

T = TypeVar("T")


class Node(ABC, Generic[T]):
    """Base class for all DAG nodes.

    Every node has a unique ``id``, a ``value_type`` for serialisation,
    and a ``changed`` signal that fires when the value updates.

    Subclasses must implement ``attempt()``.
    """

    def __init__(self, node_id: str, value_type: type[T]) -> None:
        self._id = node_id
        self._value_type = value_type
        self._adapter = TypeAdapter(value_type)
        self.changed = Signal()

    @property
    def id(self) -> str:
        return self._id

    @property
    def value_type(self) -> type[T]:
        return self._value_type

    @abstractmethod
    def attempt(self) -> Attempt[T]:
        """Return the current value with provenance."""
        ...

    def to_json(self) -> dict:
        """Serialise this node's current value + provenance.

        The API calls this method — it never accesses internal state.
        """
        attempt = self.attempt()
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

    def _impossible(self, dep_attempts: dict[str, Attempt[T]],
                    extra: str = "") -> Attempt[T]:
        """Build a detailed failure message from every failed dependency.

        Produces strings like::

            "BestLocationNode: precise_location: not set; "
            "rightmove_location: HTTP 503"
        """
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
        if prov.source_attempts:
            result["sources"] = {
                name: self._provenance_to_json(a.provenance)
                for name, a in prov.source_attempts.items()
            }
        return result
