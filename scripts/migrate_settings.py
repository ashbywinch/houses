"""Migrate old financial_source blob to individual setting nodes.

Run once after deploying the settings refactor:

    .venv/bin/python scripts/migrate_settings.py

Reads the old "financial" UserInputNode result from the DAG database
and pushes each key-value pair to the corresponding individual setting
node (settings/mortgage_rate, settings/mortgage_term, etc.).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main():
    db_path = Path("data/houses.db")
    if not db_path.exists():
        print("No database found at data/houses.db — nothing to migrate.")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Read the old financial blob
    row = conn.execute(
        "SELECT result_json FROM node_results WHERE node_id = ? ORDER BY created_at DESC LIMIT 1",
        ("financial",),
    ).fetchone()

    if row is None:
        print("No old financial settings found — nothing to migrate.")
        conn.close()
        return

    import json

    old = json.loads(row["result_json"])
    if old.get("status") != "succeeded":
        print(f"Old financial settings have status={old.get('status')} — skipping.")
        conn.close()
        return

    old_value = old.get("value")
    if not old_value or not isinstance(old_value, dict):
        print("Old financial settings value is empty or not a dict — skipping.")
        conn.close()
        return

    # Mapping from old API keys to new setting node IDs
    from houses.nodes.settings_node import API_KEY_TO_NODE
    from houses.services import _make_settings_source
    from houses.nodes.settings_node import SETTING_DEFAULTS

    pushed = 0
    for api_key, value in old_value.items():
        node_id = API_KEY_TO_NODE.get(api_key)
        if node_id is None:
            print(f"  Skipping {api_key}: no matching setting node")
            continue

        # Get or create the individual setting node
        type_info = SETTING_DEFAULTS.get(node_id)
        if type_info is None:
            print(f"  Skipping {api_key}: no type info for {node_id}")
            continue

        val_type, _ = type_info
        node = _make_settings_source(node_id, val_type, lambda: None)
        node.push(value, "migration")
        print(f"  Migrated {api_key} → {node_id}: {value}")
        pushed += 1

    conn.close()
    print(f"\nMigrated {pushed} setting(s).")


if __name__ == "__main__":
    main()
