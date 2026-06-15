from __future__ import annotations

import asyncio
import inspect
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

from houses.model import DerivedRow, NodeKind, NodeResult
from houses.model import persistence as _persistence
from houses.model.registry import NODES, get_node

logger = logging.getLogger(__name__)


def topo_sort(node_ids: list[str]) -> list[str]:
    graph: dict[str, list[str]] = {}
    for nid in node_ids:
        node = get_node(nid)
        graph[nid] = [d for d in node.deps if d in node_ids]
    in_degree: dict[str, int] = {n: len(graph[n]) for n in node_ids}
    queue = deque(n for n in node_ids if in_degree[n] == 0)
    result: list[str] = []
    while queue:
        n = queue.popleft()
        result.append(n)
        for m in node_ids:
            if n in graph.get(m, []):
                in_degree[m] -= 1
                if in_degree[m] == 0:
                    queue.append(m)
    if len(result) != len(node_ids):
        raise ValueError(f"Cycle detected among nodes: {node_ids}")
    return result


def _is_stale(
    property_id: str,
    dep_versions: dict[str, int | None],
) -> bool:
    for dep_node_id, stored_row_id in dep_versions.items():
        latest_time = _persistence.get_dep_timestamp(property_id, dep_node_id)
        if stored_row_id is None:
            if latest_time is not None:
                return True
        else:
            stored_time = _get_stored_time_for_row(dep_node_id, stored_row_id)
            if stored_time is None:
                return True
            if latest_time is None:
                continue
            if latest_time > stored_time:
                return True
    return False


def _get_stored_time_for_row(node_id: str, row_id: int) -> datetime | None:
    conn = _persistence.get_db()
    if node_id in {"corrected_address", "precise_location"}:
        table = _persistence.USER_TABLE_NODES[node_id]
        row = conn.execute(
            f"SELECT created_at FROM {table} WHERE id=?",
            (row_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT created_at FROM source_values WHERE id=?",
            (row_id,),
        ).fetchone()
    return datetime.fromisoformat(row["created_at"]) if row else None


def _expand_deps(node_ids: list[str]) -> list[str]:
    expanded = set(node_ids)
    queue = list(node_ids)
    while queue:
        nid = queue.pop()
        node = get_node(nid)
        for dep in node.deps:
            if dep not in expanded:
                expanded.add(dep)
                queue.append(dep)
    return list(expanded)


async def resolve_property(
    rid: str,
    node_ids: list[str] | None = None,
    _geocoder=None,
) -> dict[str, NodeResult]:
    data = _persistence.load_property_data(rid)
    if node_ids is None:
        node_ids = list(NODES.keys())
    ordered = topo_sort(_expand_deps(node_ids))
    results: dict[str, NodeResult] = {}
    resolved_values: dict[str, Any] = {}

    for nid in ordered:
        node = get_node(nid)
        if node.kind == NodeKind.source:
            sr = data.sources.get(nid)
            if sr:
                results[nid] = NodeResult(node_id=nid, value=sr.value, source=sr.source, row_id=sr.row_id)
                resolved_values[nid] = sr.value
            else:
                results[nid] = NodeResult(node_id=nid, value=None, source="")
                resolved_values[nid] = None
        elif node.kind == NodeKind.user_input:
            ur = data.user_inputs.get(nid)
            if ur:
                results[nid] = NodeResult(node_id=nid, value=ur.value, source="user", row_id=ur.row_id)
                resolved_values[nid] = ur.value
            else:
                results[nid] = NodeResult(node_id=nid, value=None, source="")
                resolved_values[nid] = None
        elif node.kind == NodeKind.derived:
            existing = data.derived.get(nid)
            stale = True if existing is None else _is_stale(rid, existing.dep_versions)
            if stale or existing is None:
                dep_kwargs: dict[str, Any] = {}
                dep_versions: dict[str, int | None] = {}
                for dep_id in node.deps:
                    dep_kwargs[dep_id] = resolved_values.get(dep_id)
                    dep_res = results.get(dep_id)
                    dep_versions[dep_id] = dep_res.row_id if dep_res else None
                if node.compute:
                    compute_kwargs = dict(dep_kwargs)
                    if _geocoder is not None:
                        sig = inspect.signature(node.compute)
                        if "_geocoder" in sig.parameters:
                            compute_kwargs["_geocoder"] = _geocoder
                    raw = node.compute(**compute_kwargs)
                    if asyncio.iscoroutine(raw):
                        raw = await raw
                    if isinstance(raw, tuple):
                        value, source = raw
                    else:
                        value = raw
                        source = node.provenance_template
                    now = datetime.now(UTC)
                    dr = DerivedRow(
                        value=value,
                        dep_versions=dep_versions,
                        source=source,
                        error=None,
                        updated_at=now,
                    )
                    _persistence.save_derived(rid, nid, dr)
                    results[nid] = NodeResult(node_id=nid, value=value, source=source)
                    resolved_values[nid] = value
                else:
                    results[nid] = NodeResult(node_id=nid, value=None, source="")
                    resolved_values[nid] = None
            else:
                results[nid] = NodeResult(
                    node_id=nid,
                    value=existing.value,
                    source=existing.source,
                )
                resolved_values[nid] = existing.value
    return results


def check_staleness(rid: str, node_ids: list[str]) -> dict[str, bool]:
    data = _persistence.load_property_data(rid)
    result: dict[str, bool] = {}
    for nid in node_ids:
        node = get_node(nid)
        if node.kind != NodeKind.derived:
            result[nid] = False
            continue
        existing = data.derived.get(nid)
        if existing is None:
            result[nid] = True
        else:
            result[nid] = _is_stale(rid, existing.dep_versions)
    return result
