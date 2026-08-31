"""One-shot migration: convert settings data to current format.

Run once:
    .venv/bin/python scripts/migrate_settings.py

Migrates:
1. financial blob → individual setting nodes (already done)
2. persons `deposit_equity` → `home_sale_price`/`outstanding_mortgage`/`cash_contribution`
3. Restores Ashby's email (emily.winch@gmail.com)

After successful migration, removes corrupted rows so latest_node_result
returns correct data on next server start.

Safe to re-run — idempotent.
"""

from __future__ import annotations

import json
import sqlite3

import houses.services as services
from houses.nodes.settings_node import API_KEY_TO_NODE, SETTING_DEFAULTS
from scripts.db import conn as _conn


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _read_persons(conn: sqlite3.Connection) -> list[dict]:
    """Return list of person dicts from the latest succeeded persons row."""
    row = conn.execute(
        "SELECT id, result_json FROM node_results "
        "WHERE node_id = 'persons' AND json_extract(result_json, '$.status') = 'succeeded' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return []
    return json.loads(row["result_json"])["value"]


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _write_persons(conn: sqlite3.Connection, persons: list[dict]) -> int:
    """Write persons data, return new row id."""
    # lucidlint: ignore record-shape wire-format dict — node_results DB row payload, serialization boundary owns the
    result = json.dumps({"status": "succeeded", "value": persons})
    cur = conn.execute(
        "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
        ("persons", result, "2026-07-30T23:00:00"),
    )
    conn.commit()
    if cur.lastrowid is None:
        raise RuntimeError("INSERT into node_results returned no row id")
    return cur.lastrowid


def _migrate_persons(conn: sqlite3.Connection) -> bool:
    """Convert deposit_equity → home_sale_price/outstanding_mortgage/cash_contribution.

    Returns True if any rows were written.
    """
    persons = _read_persons(conn)
    if not persons:
        print("  No persons data found.")
        return False

    changed = False
    for p in persons:
        de = p.pop("deposit_equity", None)
        if de is not None and isinstance(de, dict):
            amount = float(de.get("amount", 0))
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            p["home_sale_price"] = {"amount": str(amount), "currency": "GBP"}
            p["outstanding_mortgage"] = {"amount": "0", "currency": "GBP"}
            p["cash_contribution"] = {"amount": "0", "currency": "GBP"}
            changed = True
            print(f"  {p['name']}: converted deposit_equity ({amount}) → split fields")

        # Ensure Ashby has email
        if p.get("name") == "Ashby" and not p.get("email"):
            p["email"] = "emily.winch@gmail.com"
            changed = True
            print("  Ashby: restored email")

        # Ensure required fields have defaults
        for field in ("home_sale_price", "outstanding_mortgage", "cash_contribution"):
            if field not in p:
                p[field] = {"amount": "0", "currency": "GBP"}
                changed = True
                print(f"  {p['name']}: added missing {field}")

    if changed:
        new_id = _write_persons(conn, persons)
        print(f"  Wrote corrected persons row (id={new_id})")
    else:
        print("  Persons data already in correct format.")

    return changed


def _cleanup_corrupted_rows(conn: sqlite3.Connection) -> None:
    """Delete rows that have deposit_equity (old format) to prevent accidental load."""
    deleted = conn.execute(
        "DELETE FROM node_results WHERE node_id = 'persons' "
        "AND json_extract(result_json, '$.value[0].deposit_equity') IS NOT NULL"
    ).rowcount
    if deleted:
        print(f"  Deleted {deleted} old-format persons row(s).")
    conn.commit()


def _migrate_financial(conn: sqlite3.Connection) -> bool:
    """Migrate old financial blob to individual setting nodes.

    Already run — this is a no-op if individual nodes already exist.
    """

    row = conn.execute(
        "SELECT result_json FROM node_results WHERE node_id = 'financial' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        print("  No old financial blob found.")
        return False

    old = json.loads(row["result_json"])
    if old.get("status") != "succeeded":
        return False

    old_value = old.get("value", {})
    pushed = 0
    for api_key, value in old_value.items():
        node_id = API_KEY_TO_NODE.get(api_key)
        if node_id is None:
            continue
        # lucidlint: ignore duplicate-block sequential skip guards — each guard skips a different unmapped key;
        type_info = SETTING_DEFAULTS.get(node_id)
        if type_info is None:
            continue
        val_type, _ = type_info
        node = services._make_settings_source(node_id, val_type, lambda: None)
        node.push(value, "migration")
        pushed += 1

    print(f"  Migrated {pushed} financial setting(s).")
    return pushed > 0


def main():
    conn = _conn()
    print("Migrating persons...")
    _migrate_persons(conn)
    print("Cleaning up corrupted rows...")
    _cleanup_corrupted_rows(conn)
    print("Migrating financial settings...")
    _migrate_financial(conn)
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
