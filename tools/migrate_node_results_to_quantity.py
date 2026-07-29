"""Migrate persisted node_results from old field names to new Quantity format.

Changes:
- ``duration_minutes: <int>`` → ``duration: {"value": <int>, "unit": "minute"}``
- ``distance_km: <float>`` → ``distance: {"value": <float>, "unit": "km"}``
- ``bus_walk_penalty_minutes: <int>`` → ``bus_walk_penalty: {"magnitude": <int>, "unit": "minute"}``
- ``walk_to_town_minutes: <int>`` → ``walk_to_town: {"value": <int>, "unit": "minute"}``
"""

import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data/houses.db")


def _migrate_value(val):
    """Recursively walk a deserialized JSON value and rename fields."""
    if isinstance(val, dict):
        rewritten = {}
        for k, v in val.items():
            # `duration_minutes: N` → `duration: {"value": N, "unit": "minute"}`
            if k == "duration_minutes" and isinstance(v, (int, float)):
                rewritten["duration"] = {"value": int(v), "unit": "minute"}
                continue
            # `distance_km: N` → `distance: {"value": N, "unit": "km"}`
            if k == "distance_km" and isinstance(v, (int, float)):
                rewritten["distance"] = {"value": float(v), "unit": "km"}
                continue
            # `bus_walk_penalty_minutes: N` → `bus_walk_penalty: {"magnitude": N, "unit": "minute"}`
            if k == "bus_walk_penalty_minutes" and isinstance(v, (int, float)):
                rewritten["bus_walk_penalty"] = {"magnitude": int(v), "unit": "minute"}
                continue
            # `walk_to_town_minutes: N` → `walk_to_town: {"value": N, "unit": "minute"}`
            if k == "walk_to_town_minutes" and isinstance(v, (int, float)):
                rewritten["walk_to_town"] = {"value": int(v), "unit": "minute"}
                continue
            # `daily_cost: {"amount": float, ...}` → string amount (pre-Money migration)
            if k == "amount" and isinstance(v, (int, float)) and not isinstance(v, bool):
                rewritten[k] = str(v)
                continue
            rewritten[k] = _migrate_value(v)
        return rewritten
    if isinstance(val, list):
        return [_migrate_value(item) for item in val]
    return val


def migrate_node_results(db_path: str | Path) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    migrated = 0

    rows = conn.execute(
        "SELECT id, node_id, result_json FROM node_results "
        "WHERE result_json LIKE '%duration_minutes%' "
        "OR result_json LIKE '%distance_km%' "
        "OR result_json LIKE '%bus_walk_penalty_minutes%' "
        "OR result_json LIKE '%walk_to_town_minutes%'"
    ).fetchall()

    for row in rows:
        try:
            result = json.loads(row["result_json"])
        except (json.JSONDecodeError, TypeError) as e:
            print(f"SKIP {row['node_id']}: {e}", file=sys.stderr)
            continue

        new_result = _migrate_value(result)
        if new_result == result:
            continue

        conn.execute(
            "UPDATE node_results SET result_json = ? WHERE id = ?",
            (json.dumps(new_result), row["id"]),
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
