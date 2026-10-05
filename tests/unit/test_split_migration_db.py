"""DB-level contract for the node_results split migration.

Against a synthesized copy of the real pre-split schema (one zlib'd
``result_json`` per row), not just the pure transform: the migration must
convert every row into the split columns, keep the legacy blob readable until
then, drop the column once done, and be idempotent — the runner calls it on
the standby's database at install and on the restored snapshot at cutover.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import zlib
from pathlib import Path

from scripts import split_node_results as migration

SCHEMA = """
CREATE TABLE node_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    dep_timestamps TEXT,
    created_at TEXT NOT NULL,
    code_version TEXT
)
"""


def _legacy_db(tmp_path) -> str:
    """A pre-split database with the three row shapes that exist in the wild."""
    path = tmp_path / "houses.db"
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    records = {
        "p1/walkability": {
            "status": "succeeded",
            "value": {"walk_to_town": 11},
            "provenance": {"label": "walkability", "tree": {"deep": ["x" * 400]}},
        },
        "p1/bus_augment": {"status": "impossible", "error": "no route", "error_detail": {"code": "no_route"}},
        "p1/status": {"status": "succeeded", "value": "current", "source_label": "user"},
    }
    for node_id, record in records.items():
        conn.execute(
            "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
            (node_id, zlib.compress(json.dumps(record).encode("utf-8")), "2026-10-01T00:00:00+00:00"),
        )
    # One row written by an older, uncompressed writer.
    conn.execute(
        "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
        ("p2/postcode", json.dumps({"status": "succeeded", "value": "RG18 4DQ"}), "2026-10-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return str(path)


def test_apply_converts_every_row_and_drops_the_legacy_column(tmp_path):
    db = _legacy_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    assert migration.count_pending(conn) == 4

    result = migration.apply_migration(conn, backup_path=None, verify=True)

    assert result.ok and result.applied == 4 and result.dropped_column
    columns = {row[1] for row in conn.execute("PRAGMA table_info(node_results)")}
    assert "result_json" not in columns, "the legacy blob column is gone"

    row = conn.execute("SELECT * FROM node_results WHERE node_id='p1/walkability'").fetchone()
    assert row["status"] == "succeeded"
    assert json.loads(row["value_json"]) == {"walk_to_town": 11}
    assert "x" * 400 not in row["value_json"], "the value column carries no provenance"
    tree = json.loads(zlib.decompress(row["provenance_z"]))
    assert tree == {"label": "walkability", "tree": {"deep": ["x" * 400]}}

    failed = conn.execute("SELECT * FROM node_results WHERE node_id='p1/bus_augment'").fetchone()
    assert failed["status"] == "impossible"
    assert failed["error"] == "no route"
    assert json.loads(failed["error_detail_json"]) == {"code": "no_route"}

    user_input = conn.execute("SELECT * FROM node_results WHERE node_id='p1/status'").fetchone()
    assert user_input["source_label"] == "user", "the hot label column is populated"

    plain = conn.execute("SELECT * FROM node_results WHERE node_id='p2/postcode'").fetchone()
    assert json.loads(plain["value_json"]) == "RG18 4DQ", "uncompressed legacy rows convert too"
    conn.close()

    # Idempotent: a second run has nothing to do and says so.
    again = sqlite3.connect(db)
    assert migration.count_pending(again) == 0
    again.close()


def _run_check(db: str) -> int:
    """Run the check the way the rollout runner does: as a program."""
    import subprocess

    script = Path(__file__).resolve().parents[2] / "scripts" / "split_node_results.check.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--db", db], capture_output=True, text=True, check=False
    )
    return completed.returncode


def test_the_check_passes_only_after_the_split(tmp_path):
    db = _legacy_db(tmp_path)
    assert _run_check(db) == 1, "a pre-split database is not complete"

    conn = sqlite3.connect(db)
    migration.apply_migration(conn, backup_path=None, verify=True)
    conn.close()

    assert _run_check(db) == 0


def test_rows_that_are_not_json_are_reported_not_dropped(tmp_path):
    """An unreadable row is a human decision: the apply stops and keeps it."""
    db = _legacy_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
        ("p3/broken", b"\x78\x9cnot-a-zlib-stream", "2026-10-01T00:00:00+00:00"),
    )
    conn.commit()

    result = migration.apply_migration(conn, backup_path=None, verify=False)

    assert not result.ok
    columns = {row[1] for row in conn.execute("PRAGMA table_info(node_results)")}
    assert "result_json" in columns, "the legacy column stays while a row is unconverted"
    conn.close()


def test_a_database_without_node_results_is_a_no_op(tmp_path):
    path = tmp_path / "empty.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE comments (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    assert migration.run(
        migration.RunOptions(db=str(path), apply=True, backup=False, backup_path=None, verify=False)
    ) == 0, "a database without node_results is a no-op, not a failure"
