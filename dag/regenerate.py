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
from dag.scheduler import flush_processor


def pattern_regex(pattern: str) -> re.Pattern[str]:
    """Compile a node-id pattern: ``*`` matches any run of characters."""
    escaped = re.escape(pattern).replace("\\*", ".*")
    return re.compile(f"^{escaped}$")


def nodes_matching(patterns: Iterable[str], nodes: Iterable[Node]) -> list[Node]:
    """All nodes whose id matches ANY pattern."""
    regexes = [pattern_regex(p) for p in patterns]
    return [n for n in nodes if any(rx.match(n._id) for rx in regexes)]


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
            skipped.append({"node": node._id, "reason": "input node — no computation"})
            continue
        await node.refresh(force=True)
        regenerated.append({"node": node._id, "status": node.latest_attempt().status})
    # Dependents of the regenerated nodes are now stale — drain the
    # scheduler so the response reflects the completed cascade.
    await flush_processor()
    return regenerated, skipped
