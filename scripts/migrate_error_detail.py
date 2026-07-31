"""One-shot migration: backfill error_detail on legacy impossible rows.

Rows persisted before the AttemptError work carry only a flat error
string (the node-id/dep chain) and no structured error_detail. This
migration reconstructs error_detail structurally from the persisted
provenance tree — each source with an error becomes a cause, recursing
to the friendly leaf — so display_message resolves to the leaf reason
instead of leaking node ids / 'dep failed' into the UI.

Purely structural: never parses error strings.

Run once:
    .venv/bin/python scripts/migrate_error_detail.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _conn() -> sqlite3.Connection:
    db = Path("data/houses.db")
    if not db.exists():
        raise SystemExit("data/houses.db not found")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _walk_errors(prov: dict) -> list[dict]:
    """Walk a provenance dict; return the error-bearing nodes as
    (error, sources-with-errors) pairs, root first."""
    err = prov.get("error") or prov.get("description")
    sources = prov.get("sources") or {}
    child_errors = []
    for s in sources.values():
        child_errors.extend(_walk_errors(s))
    return [{"message": err, "children": child_errors}] if err else child_errors


def _to_error_detail(node: dict, source: str = "") -> dict:
    """Build an error_detail dict from a provenance-tree node.

    A node with children whose errors are chains is a dep_failed parent;
    a leaf error is the friendly message (no_data). Children become
    causes, recursively.
    """
    children = [_to_error_detail(c, source) for c in node["children"]]
    if children:
        return {
            "code": "dep_failed",
            "message": node["message"],
            "user_message": children[0]["user_message"],
            "retryable": False,
            "source": source,
            "exc_type": "",
            "traceback": "",
            "causes": children,
        }
    return {
        "code": "no_data",
        "message": node["message"],
        "user_message": node["message"],
        "retryable": False,
        "source": source,
        "exc_type": "",
        "traceback": "",
        "causes": [],
    }


def _migrate(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT id, node_id, result_json FROM node_results "
        "WHERE json_extract(result_json, '$.status') = 'impossible' "
        "AND json_extract(result_json, '$.error_detail.code') IS NULL"
    ).fetchall()
    updated = 0
    for row in rows:
        data = json.loads(row["result_json"])
        prov = data.get("provenance") or {}
        tree = _walk_errors(prov)
        if not tree:
            continue  # no provenance tree to walk — leave as-is
        detail = _to_error_detail(tree[0], source=row["node_id"])
        data["error_detail"] = detail
        # Keep the flat error for logs; display_message now resolves
        # through the reconstructed causes.
        conn.execute(
            "UPDATE node_results SET result_json = ? WHERE id = ?",
            (json.dumps(data), row["id"]),
        )
        updated += 1
    conn.commit()
    return updated


def main():
    conn = _conn()
    n = _migrate(conn)
    conn.close()
    print(f"Backfilled error_detail on {n} impossible row(s).")


if __name__ == "__main__":
    main()
