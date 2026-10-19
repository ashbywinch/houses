"""Wiring record for ``IfThenElseNode``: the branch predicate and sources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dag.node import Node


@dataclass(frozen=True)
class IfThenElseOptions:
    """Wiring for an ``IfThenElseNode``: the branch predicate and sources.

    ``condition_fn`` receives the condition sources' ``Attempt`` values (in
    order); ``then_branch``/``else_branch`` provide the activated result.
    """

    condition_sources: tuple[Node, ...]
    condition_fn: Callable[..., bool]
    then_branch: Node[Any]
    else_branch: Node[Any] | None = None
