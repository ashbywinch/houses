"""Migrate persisted node_results from old field names to new Quantity format.

Changes:
- ``duration_minutes: <int>`` → ``duration: {"value": <int>, "unit": "minute"}``
- ``distance_km: <float>`` → ``distance: {"value": <float>, "unit": "km"}``
- ``bus_walk_penalty_minutes: <int>`` → ``bus_walk_penalty: {"value": <int>, "unit": "minute"}``
- ``walk_to_town_minutes: <int>`` → ``walk_to_town: {"value": <int>, "unit": "minute"}``
"""

import json
import sqlite3
import sys
from pathlib import Path

from dag.persistence import compress_result, decompress_result

DB_PATH = Path("data/houses.db")


def _migrate_value(val):
    """Recursively walk a deserialized JSON value and rename fields."""
    if isinstance(val, dict):
        rewritten = {}
        _rewrite_legacy_fields(rewritten, val)
        return rewritten
    if isinstance(val, list):
        return [_migrate_value(item) for item in val]
    return val


def _rewrite_legacy_fields(rewritten, val):
    for k, v in val.items():
        # `duration_minutes: N` → `duration: {"value": N, "unit": "minute"}`
        if k == "duration_minutes" and isinstance(v, (int, float)):
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            rewritten["duration"] = {"value": int(v), "unit": "minute"}
            continue
        # `distance_km: N` → `distance: {"value": N, "unit": "km"}`
        if k == "distance_km" and isinstance(v, (int, float)):
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            rewritten["distance"] = {"value": float(v), "unit": "km"}
            continue
        # `bus_walk_penalty_minutes: N` → `bus_walk_penalty: {"value": N, "unit": "minute"}`
        if k == "bus_walk_penalty_minutes" and isinstance(v, (int, float)):
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            rewritten["bus_walk_penalty"] = {"value": int(v), "unit": "minute"}
            continue
        # `walk_to_town_minutes: N` → `walk_to_town: {"value": N, "unit": "minute"}`
        if k == "walk_to_town_minutes" and isinstance(v, (int, float)):
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            rewritten["walk_to_town"] = {"value": int(v), "unit": "minute"}
            continue
        # `daily_cost: {"amount": float, ...}` → string amount (pre-Money migration)
        if k == "amount" and isinstance(v, (int, float)) and not isinstance(v, bool):
            rewritten[k] = str(v)
            continue
        # `{"magnitude": N, ...}` → `{"value": N, ...}` (first migration pass used "magnitude")
        if k == "magnitude":
            rewritten["value"] = _migrate_value(v)
            continue
        rewritten[k] = _migrate_value(v)


def migrate_node_results(db_path: str | Path) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    migrated = 0

    # result_json is zlib-compressed on disk, so a SQL LIKE on the raw
    # BLOB matches nothing (review finding). Select ALL rows and pattern-
    # match the decompressed JSON in Python instead.
    patterns = (
        "duration_minutes",
        "distance_km",
        "bus_walk_penalty_minutes",
        "walk_to_town_minutes",
        "magnitude",
    )
    rows = conn.execute(
        "SELECT id, node_id, result_json FROM node_results"
    ).fetchall()

    for row in rows:
        try:
            text = decompress_result(row["result_json"])
        except (UnicodeDecodeError, TypeError) as e:
            print(f"SKIP {row['node_id']}: {e}", file=sys.stderr)
            continue
        if not any(p in text for p in patterns):
            continue
        try:
            result = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"SKIP {row['node_id']}: {e}", file=sys.stderr)
            continue

        new_result = _migrate_value(result)
        if new_result == result:
            continue

        conn.execute(
            "UPDATE node_results SET result_json = ? WHERE id = ?",
            (compress_result(json.dumps(new_result)), row["id"]),
        )
        migrated += 1

    conn.commit()
    conn.close()
    return migrated


def main() -> None:
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        sys.exit(0)

    count = migrate_node_results(DB_PATH)
    print(f"Migrated {count} node_results rows to Quantity format")


if __name__ == "__main__":
    main()
