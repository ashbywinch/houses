"""One-shot migration: clear impossible node_results so the DAG recomputes them.

Rows persisted before the AttemptError work carry only a flat error string
and no structured error_detail. Rather than reconstructing error_detail
from persisted data (which can't recover the leaf reason reliably), clear
the impossible rows — the affected nodes become pending on next load and
recompute with current code, which emits error_detail natively with the
correct causes chain.

This is the documented forward path (docs/development.md → "Fixing Bugs
That Produced Wrong Persisted Data"): delete the errored rows, let the
cascade propagate. Only impossible rows are cleared — succeeded values
are untouched.

Run once, then trigger a server reload so nodes re-load as pending:
    .venv/bin/python scripts/migrate_error_detail.py
    touch houses/server.py
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


def _clear_impossible(conn: sqlite3.Connection) -> int:
    """Delete impossible node_results rows; keep succeeded rows."""
    cur = conn.execute(
        "DELETE FROM node_results WHERE json_extract(result_json, '$.status') = 'impossible'"
    )
    conn.commit()
    return cur.rowcount


def _migrate_works_estimates(conn: sqlite3.Connection) -> int:
    """Convert works_estimates values from raw numbers to Money shape.

    The Money rule applies to all monetary values; per-person works
    estimates are money. Old rows stored {"Ashby": 20000} — convert to
    {"Ashby": {"amount": "20000", "currency": "GBP"}}.
    """
    rows = conn.execute(
        "SELECT id, node_id, result_json FROM node_results "
        "WHERE node_id LIKE '%/works_estimates' "
        "AND json_extract(result_json, '$.status') = 'succeeded'"
    ).fetchall()
    updated = 0
    for row in rows:
        data = json.loads(row["result_json"])
        value = data.get("value") or {}
        if not isinstance(value, dict):
            continue
        changed = False
        for name, val in list(value.items()):
            is_numeric_str = isinstance(val, str) and val.replace(".", "").replace("-", "").isdigit()
            if isinstance(val, (int, float)) or is_numeric_str:
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
                value[name] = {"amount": str(val), "currency": "GBP"}
                changed = True
        if changed:
            data["value"] = value
            conn.execute(
                "UPDATE node_results SET result_json = ? WHERE id = ?",
                (json.dumps(data), row["id"]),
            )
            updated += 1
    conn.commit()
    return updated


def main():
    conn = _conn()
    n = _clear_impossible(conn)
    m = _migrate_works_estimates(conn)
    conn.close()
    print(f"Cleared {n} impossible row(s). Nodes will recompute on reload.")
    print(f"Converted {m} works_estimates row(s) to Money.")


if __name__ == "__main__":
    main()
