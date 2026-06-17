from __future__ import annotations

from abc import abstractmethod
from typing import Generic, TypeVar

from dag.attempt import Attempt
from dag.node import Node
from dag.signals import Slot

T = TypeVar("T")


class ComputedNode(Node[T], Generic[T]):
    """A node whose value is computed from other nodes.

    Subclasses declare their dependencies and implement ``compute()``.
    The node subscribes to each dep's ``changed`` signal and re-computes
    when any dep updates. Results are cached until a dep signals a change.
    """

    def __init__(self, node_id: str, value_type: type[T],
                 deps: tuple[Node, ...]) -> None:
        super().__init__(node_id, value_type)
        self._deps = deps
        self._cached: Attempt[T] | None = None
        self._dirty = True
        self._slots: list[Slot] = []

        for dep in deps:
            slot = Slot(self._on_dep_changed)
            self._slots.append(slot)
            dep.changed.connect(slot)

    def _on_dep_changed(self) -> None:
        self._dirty = True
        self.changed.emit()

    def attempt(self) -> Attempt[T]:
        if self._dirty:
            dep_attempts = [dep.attempt() for dep in self._deps]
            self._cached = self.compute(*dep_attempts)
            self._dirty = False
        return self._cached

    @abstractmethod
    def compute(self, *dep_attempts: Attempt) -> Attempt[T]:
        """Subclasses implement this.

        Receives the Attempt values of each dependency. Returns a new
        Attempt with composed provenance.
        """
        ...
