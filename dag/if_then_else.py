"""Generic conditional node — activates then/else branch based on a predicate."""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node

T = TypeVar("T")


class IfThenElseNode(DerivedNode[T], Generic[T]):
    """A node that conditionally activates either a then-branch or an
    else-branch based on a synchronous predicate over condition sources.

    Both branches produce the same type ``T``.  The predicate receives
    ``Attempt`` values for each condition source (in order).
    """

    def __init__(
        self,
        node_id: str,
        value_type: type[T],
        *,
        condition_sources: tuple[Node, ...],
        condition_fn: Callable[..., bool],
        then_branch: Node[T],
        else_branch: Node[T] | None = None,
    ) -> None:
        self._condition_sources = condition_sources
        self._condition_fn = condition_fn
        self._then_branch = then_branch
        self._else_branch = else_branch

        deps = condition_sources + (then_branch,)
        if else_branch is not None:
            deps = deps + (else_branch,)
        super().__init__(node_id, value_type, deps)

    def _get_active_deps(self) -> tuple[Node, ...]:
        condition_values = [s.latest_attempt() for s in self._condition_sources]
        if self._condition_fn(*condition_values):
            return self._condition_sources + (self._then_branch,)
        if self._else_branch is not None:
            return self._condition_sources + (self._else_branch,)
        return self._condition_sources

    def compute(self, *args: Attempt) -> Attempt[T]:
        # If a branch was activated, the last arg is the branch result.
        if len(args) > len(self._condition_sources):
            return args[-1]
        # No branch activated — return succeeded(None).  The node's value_type
        # must be a nullable type (T | None) so the TypeAdapter round-trips
        # correctly through persist/reload.
        return Attempt.succeeded(None)
