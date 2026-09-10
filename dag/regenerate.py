"""Force-regeneration of DAG nodes (Part D follow-up, 2026-08-06).

Code changes are now detected automatically: every persisted result
carries a code-version fingerprint, and a mismatch recomputes on the
next refresh (see ``DerivedNode.code_is_stale``).  ``force_regenerate``
remains for explicit recomputes — a full refresh right now, or a
recompute whose inputs changed but whose dep timestamps don't reflect
it.  It bypasses the staleness check and recomputes the matched nodes
through the normal refresh path (persist + signals), so the cascade to
dependents works as usual.


Patterns are glob-style over node ids: ``*`` matches any run of
characters (including ``/``). A pattern with no ``*`` is an exact id.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from dag.derived_node import DerivedNode
from dag.node import Node
from dag.scheduler import flush_processor, get_scheduler


def pattern_regex(pattern: str) -> re.Pattern[str]:
    """Compile a node-id pattern: ``*`` matches any run of characters."""
    escaped = re.escape(pattern).replace("\\*", ".*")
    return re.compile(f"^{escaped}$")


def nodes_matching(patterns: Iterable[str], nodes: Iterable[Node]) -> list[Node]:
    """All nodes whose id matches ANY pattern."""
    regexes = [pattern_regex(p) for p in patterns]
    return [n for n in nodes if any(rx.match(n._id) for rx in regexes)]


# lucidlint: ignore record-shape wire-format dict — serialization boundary
async def force_regenerate(nodes: Iterable[Node]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Force-recompute every matched DerivedNode and drain the cascade.

    Returns:
        ``(regenerated, skipped)`` — regenerated: ``{node, status}`` per
        recomputed node; skipped: ``{node, reason}`` for matches that
        have no computation (UserInputNodes).
    """
    regenerated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, DerivedNode):
            # lucidlint: ignore record-shape wire-format dict — admin API response payload, serialization boundary owns
            skipped.append({"node": node._id, "reason": "input node — no computation"})
            continue
        await node.refresh(force=True)
        # lucidlint: ignore record-shape wire-format dict — admin API response payload, serialization boundary owns the
        regenerated.append({"node": node._id, "status": node.latest_attempt().status})
    # Dependents of the regenerated nodes are now stale — drain the
    # scheduler so the response reflects the completed cascade.
    await flush_processor()
    return regenerated, skipped


def schedule_code_stale_nodes(roots: Iterable[Node]) -> list[DerivedNode]:
    """Schedule every DerivedNode reachable from ``roots`` whose persisted
    result was produced by different code — or which disagrees with its
    dependencies' current results (a start-up race) — and return them.

    The walk follows EVERY dependency (``deps_for_traversal``), never the
    node's active dep set.  An active set narrows on purpose — a wrapper
    drops a failed pipeline so the failure cannot propagate, a conditional
    node evaluates one branch — and a refresh walk that inherited the
    narrowing could never reach the hidden node, so its persisted result
    would stay for good.  That is exactly how a transient crash while the
    module was mid-edit became permanent, unpriced school commutes on the
    live pages (2026-09-10): the sweep saw the wrapper, whose active deps
    were empty, and stopped.
    """
    seen: set[int] = set()
    queue: list[Node] = list(roots)
    stale: list[DerivedNode] = []
    while queue:
        node = queue.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, DerivedNode):
            if node.code_is_stale() or node.needs_refresh():
                get_scheduler().schedule(node)
                stale.append(node)
            queue.extend(node.deps_for_traversal())
    return stale
