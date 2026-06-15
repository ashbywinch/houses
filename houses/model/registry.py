from __future__ import annotations

from typing import Any

from houses.model import NodeDef, NodeKind

NODES: dict[str, NodeDef] = {}


def node(
    id: str,
    kind: NodeKind,
    *,
    deps: list[str] | None = None,
    provenance_template: str = "",
    user_table: str | None = None,
) -> Any:
    def decorator(func):
        nd = NodeDef(
            id=id,
            kind=kind,
            deps=deps or [],
            compute=func if kind == NodeKind.derived else None,
            provenance_template=provenance_template,
            user_table=user_table,
        )
        NODES[id] = nd
        return func

    return decorator


def get_node(node_id: str) -> NodeDef:
    if node_id not in NODES:
        raise KeyError(f"Unknown node: {node_id}")
    return NODES[node_id]
