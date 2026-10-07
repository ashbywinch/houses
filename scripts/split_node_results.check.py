"""Check that node_results is fully split (read-only; exits 0 iff complete).

A separate program from the apply, so it cannot vouch for the code that just
wrote: it opens the database read-only and verifies the effect.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

EXIT_COMPLETE = 0
EXIT_INCOMPLETE = 1
EXIT_UNSCANNABLE = 2

DB_PATH = "data/houses.db"

_SPLIT_COLUMNS = ("status", "value_json", "provenance_z")


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(node_results)")}


def check(db_path: str) -> int:
    """Verify the split for one database; the exit code IS the verdict."""
    db = Path(db_path)
    if not db.is_file():
        print(f"check: database not found: {db}", file=sys.stderr)
        return EXIT_UNSCANNABLE
    # mode=ro: the check must not be able to write, even by accident.
    conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='node_results'"
        ).fetchone():
            print("check INCOMPLETE: no node_results table", file=sys.stderr)
            return EXIT_INCOMPLETE
        columns = _columns(conn)
        missing = [c for c in _SPLIT_COLUMNS if c not in columns]
        if missing:
            print(f"check INCOMPLETE: split columns missing: {missing}", file=sys.stderr)
            return EXIT_INCOMPLETE
        if "result_json" in columns:
            print("check INCOMPLETE: the legacy result_json column is still present", file=sys.stderr)
            return EXIT_INCOMPLETE
        unset = int(conn.execute("SELECT COUNT(*) FROM node_results WHERE status IS NULL").fetchone()[0])
        if unset:
            print(f"check INCOMPLETE: {unset} rows have no status", file=sys.stderr)
            return EXIT_INCOMPLETE
        # The JSON columns must parse — a sample, since the full table is
        # hundreds of thousands of rows and this check runs on every install.
        sample = conn.execute(
            "SELECT value_json, error_detail_json, extra_json FROM node_results"
            " WHERE value_json IS NOT NULL OR error_detail_json IS NOT NULL OR extra_json IS NOT NULL"
            " LIMIT 200"
        ).fetchall()
        for row in sample:
            for column in ("value_json", "error_detail_json", "extra_json"):
                if row[column] is not None:
                    json.loads(row[column])
    except sqlite3.Error as exc:
        print(f"check: cannot scan {db} ({exc})", file=sys.stderr)
        return EXIT_UNSCANNABLE
    finally:
        conn.close()

    print(f"check ok: node_results split (columns present, no legacy blob, {len(sample)} rows parsed)")
    return EXIT_COMPLETE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DB_PATH, help="sqlite path (default: data/houses.db)")
    return check(parser.parse_args().db)


if __name__ == "__main__":
    raise SystemExit(main())
