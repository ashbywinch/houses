"""Migrate old node_results values to Money dict format.

Changes in phase2-base-module:
- rightmove_price: UserInputNode[str] -> UserInputNode[Money]
- stamp_duty, monthly_mortgage, yearly_sinking_fund, total_monthly_housing_cost:
  DerivedNode[float] -> DerivedNode[Money]

Old persisted values (string or float) won't validate against TypeAdapter[Money].
This rewrites them to {"amount": <value>, "currency": "GBP"}.
"""

import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

MONEY_NODES = {
    "rightmove_price",
    "stamp_duty",
    "monthly_mortgage",
    "yearly_sinking_fund",
    "total_monthly_housing_cost",
}

DB_PATH = Path("data/houses.db")


def migrate_node_results(db_path: str | Path) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    migrated = 0

    rows = conn.execute("SELECT id, node_id, result_json FROM node_results").fetchall()

    for row in rows:
        node_id: str = row["node_id"]
        if not any(node_id.endswith("/" + name) or node_id == name for name in MONEY_NODES):
            continue

        try:
            result = json.loads(row["result_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        value = result.get("value")
        if value is None:
            continue

        if isinstance(value, dict) and "amount" in value and "currency" in value:
            continue

        if isinstance(value, (str, float, int)):
            try:
                amount = str(Decimal(str(value)))
            except (ValueError, TypeError):
                continue
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            result["value"] = {"amount": amount, "currency": "GBP"}
        else:
            continue

        conn.execute(
            "UPDATE node_results SET result_json = ? WHERE id = ?",
            (json.dumps(result), row["id"]),
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
    print(f"Migrated {count} node_results rows to Money format")


if __name__ == "__main__":
    main()
