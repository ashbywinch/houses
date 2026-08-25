"""Pure scenario evaluation — what-if without persisting (Part D).

``evaluate(targets, overrides)`` answers "what would these nodes be if
these inputs were different?" without touching the scheduler, the
database, signals, or any real node state. Hypothetical attempts are
staged in a task-local context that ``latest_attempt()`` consults, so
compute bodies written against the real read path (expressions, ``Ref``,
predicates, ``_get_active_deps``) work unchanged.

Design rules (from the plan, settled 2026-08-06):

- Library-level and houses-agnostic. Overrides are keyed by node id
  (``node_id -> candidate value``); which nodes are override-able is
  registered per project (the houses side), keeping this generic.
- Only the dependency closure of the targets is computed; unrelated
  nodes are never recomputed.
- Overridden inputs carry ``metadata={"hypothetical": True}`` so
  provenance can render "not saved".
- The real refresh path is untouched: same graph, same ``compute``,
  no persistence.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from inspect import iscoroutine
from typing import Any

from dag.attempt import Attempt, AttemptError
from dag.derived_node import DerivedNode
from dag.eval_context import build_staging, eval_staging_ctx
from dag.node import Node

logger = logging.getLogger(__name__)


async def _compute_attempt(node: DerivedNode, dep_attempts: list[Attempt], active: tuple[Node, ...]) -> Attempt:
    """Run one node's compute in a what-if evaluation, converting failures
    into impossible Attempts (never raising into the caller)."""
    try:
        result = node._call_compute(dep_attempts, active)
        if iscoroutine(result):
            result = await result
        return result
    # lucidlint: ignore broad-except boundary — what-if compute failures convert to impossible Attempts, never raise
    except Exception as e:
        logger.debug("what-if compute failed for %s: %s", node._id, e)
        return Attempt.impossible(
            f"{node._id}: {e}",
            error_info=AttemptError.from_exception(str(e), e, source=node._id),
        )


def _closure(targets: Iterable[Node]) -> list[Node]:
    """All nodes reachable from the targets via deps, dependencies-first.

    Cycle-guarded: the production graph is a DAG; the guard only
    prevents an infinite loop on a malformed one.
    """
    visited: dict[str, Node] = {}
    order: list[Node] = []

    def visit(node: Node) -> None:
        if node._id in visited:
            return
        visited[node._id] = node
        for dep in getattr(node, "_deps", ()):
            visit(dep)
        order.append(node)

    for target in targets:
        visit(target)
    return order
async def _stage_node(node: Node, staging: dict[str, Attempt]) -> None:
    """Stage one node's hypothetical attempt when a dep changed.

    Mirrors the refresh path: never call compute with pending/impossible
    deps (node compute asserts would otherwise misreport a dependency
    problem as a compute failure).  Nodes whose inputs are unchanged keep
    their real attempts — a what-if over two fields must not re-run
    unrelated pipeline nodes (e.g. transit lookups).
    """
    if node._id in staging:
        return
    if not isinstance(node, DerivedNode):
        # UserInputNode without an override keeps its real value —
        # nothing to stage.
        return
    active = node._get_active_deps()
    # Incremental: only recompute when a dep is staged (overridden or
    # itself recomputed).
    if not any(dep._id in staging for dep in active):
        return
    dep_attempts = [dep.latest_attempt() for dep in active]
    if any(a.pending for a in dep_attempts):
        staging[node._id] = Attempt.pending()
        return
    impossible_deps = [a for a in dep_attempts if a.impossible]
    if impossible_deps:
        errors = "; ".join(a.error or "unknown" for a in impossible_deps)
        staging[node._id] = Attempt.impossible(f"{node._id}: dep failed ({errors})")
        return
    staging[node._id] = await _compute_attempt(node, dep_attempts, active)


def _staged_results(target_nodes: list[Node], staging: dict[str, Attempt]) -> dict[str, Attempt]:
    """{target id: hypothetical attempt} — real attempts for unstaged targets."""
    return {target._id: staging.get(target._id) or target.latest_attempt() for target in target_nodes}


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def evaluate(targets: Node | Iterable[Node], overrides: dict[str, Any] | None = None) -> dict[str, Attempt]:
    """Evaluate target nodes under input overrides — pure and throwaway.

    Args:
        targets: a Node or an iterable of Nodes whose values are wanted.
        overrides: ``node_id -> candidate value`` for any node in the
            graph (typically UserInputNodes). Overridden inputs are
            wrapped in succeeded Attempts marked hypothetical.

    Returns:
        ``{node_id: Attempt}`` for each target, computed against the
        staged overrides. Nothing is persisted; real node state is
        unchanged.
    """
    target_nodes = list(targets) if not isinstance(targets, Node) else [targets]
    staging = build_staging(overrides or {})
    token = eval_staging_ctx.set(staging)
    try:
        for node in _closure(target_nodes):
            await _stage_node(node, staging)
        return _staged_results(target_nodes, staging)
    finally:
        eval_staging_ctx.reset(token)
