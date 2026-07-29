#!/usr/bin/env python
"""Migrate old sheet comments to the comments table for all registered properties.

Run once after deploying the new comments architecture to ensure all
old-style comments (``group_notes``, ``ashby_comments``) are in the
comments table:

    uv run python tools/migrate_comments.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from houses.config import settings


def _get_dag_db() -> sqlite3.Connection:
    """Open the DAG database directly — no server startup needed."""
    path = Path(settings.sqlite_path)
    if not path.exists():
        print(f"Database not found: {path}")
        raise SystemExit(1)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def get_property_rids(dag: sqlite3.Connection) -> list[str]:
    """Extract unique property RIDs from DAG node IDs."""
    rows = dag.execute(
        "SELECT DISTINCT substr(node_id, 1, instr(node_id, '/') - 1) AS rid "
        "FROM node_results WHERE node_id LIKE '%/%'"
    ).fetchall()
    rids = sorted({row["rid"] for row in rows if row["rid"]})
    return rids


def get_old_comments(dag: sqlite3.Connection, rid: str) -> dict:
    """Read old-style comment nodes from the DAG database."""
    result: dict = {}
    for field in ("group_notes", "ashby_comments"):
        row = dag.execute(
            "SELECT result_json FROM node_results WHERE node_id = ?",
            (f"{rid}/{field}",),
        ).fetchone()
        if row is not None:
            try:
                data = json.loads(row["result_json"])
                result[field] = data
            except (json.JSONDecodeError, TypeError):
                pass
    return result


def count_comments_in_db(conn: sqlite3.Connection, rid: str) -> int:
    """Check how many comments exist for a property in the comments table."""
    return conn.execute(
        "SELECT COUNT(*) FROM comments WHERE rid = ?",
        (rid,),
    ).fetchone()[0]


def main() -> None:
    db_path = Path(settings.sqlite_path)
    print(f"DAG database: {db_path}")

    # Initialise the comments table
    from houses.database import get_connection as get_app_connection
    from houses.database import init_db

    init_db()
    app_conn = get_app_connection()
    total_before = app_conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    print(f"Comments already in DB: {total_before}")

    dag = _get_dag_db()
    rids = get_property_rids(dag)
    print(f"Properties found in DAG: {len(rids)}")

    if not rids:
        print("No properties to migrate.")
        return

    # Read old comments from DAG and migrate each property
    from houses.comments import migrate_old_comments

    migrated = 0
    comment_rows = 0
    skipped = 0

    for rid in rids:
        # Check how many comments already in DB for this rid
        existing = count_comments_in_db(app_conn, rid)
        if existing > 0:
            # Already has comments — migration might already have run
            # Still call migrate_old_comments — it's idempotent and
            # double-checks internally
            pass

        old = get_old_comments(dag, rid)
        result = migrate_old_comments(rid, old)

        new_for_rid = len([
            c for c in result
            if isinstance(c, dict) and c.get("timestamp", "").startswith("1980")
        ])
        if new_for_rid > 0:
            migrated += 1
            comment_rows += new_for_rid
            print(f"  ✓ {rid}: {new_for_rid} old comments migrated (total: {len(result)})")
        else:
            skipped += 1

    total_after = app_conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    print(f"\nDone. Migrated: {migrated}, Skipped (already done / empty): {skipped}")
    print(f"Total comments in DB: {total_before} → {total_after}")
    dag.close()


if __name__ == "__main__":
    main()
