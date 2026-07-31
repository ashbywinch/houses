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


def main():
    conn = _conn()
    n = _clear_impossible(conn)
    conn.close()
    print(f"Cleared {n} impossible row(s). Nodes will recompute on reload.")


if __name__ == "__main__":
    main()
