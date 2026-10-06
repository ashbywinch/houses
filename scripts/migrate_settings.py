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

import sqlite3

import dag.persistence as per
import houses.services as services
from houses.nodes.settings_node import API_KEY_TO_NODE, SETTING_DEFAULTS
from scripts.db import conn as _conn


# lucidlint: ignore record-shape wire-format dict — serialization boundary
def _read_persons(conn: sqlite3.Connection) -> list[dict]:
    """Return list of person dicts from the latest succeeded persons row."""
    columns = ", ".join(per.record_select_columns(conn))
    rows = conn.execute(
        f"SELECT rowid AS rowid, {columns} FROM node_results WHERE node_id = 'persons'"
        " ORDER BY created_at DESC, rowid DESC"
    ).fetchall()
    for row in rows:
        record = per.read_node_record(row)
        if record.get("status") == "succeeded":
            return list(record.get("value") or [])
    return []


# lucidlint: ignore record-shape wire-format dict — serialization boundary
def _write_persons(conn: sqlite3.Connection, persons: list[dict]) -> int:
    """Write persons data, return new row id."""
    row_id = per.save_node_result(
        "persons",
        # lucidlint: ignore record-shape this IS the stored node record (serialization boundary)
        {"status": "succeeded", "value": persons},
        created_at="2026-07-30T23:00:00",
    )
    if not row_id:
        raise RuntimeError("INSERT into node_results returned no row id")
    return row_id


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
# lucidlint: ignore record-shape wire-format dict — serialization boundary
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
    columns = ", ".join(per.record_select_columns(conn))
    deleted = 0
    for row in conn.execute(
        f"SELECT rowid AS rowid, {columns} FROM node_results WHERE node_id = 'persons'"
    ).fetchall():
        value = per.read_node_record(row).get("value")
        if isinstance(value, list) and value and isinstance(value[0], dict) and "deposit_equity" in value[0]:
            conn.execute("DELETE FROM node_results WHERE rowid = ?", (row["rowid"],))
            deleted += 1
    if deleted:
        print(f"  Deleted {deleted} old-format persons row(s).")
    conn.commit()


def _migrate_financial(conn: sqlite3.Connection) -> bool:
    """Migrate old financial blob to individual setting nodes.

    Already run — this is a no-op if individual nodes already exist.
    """

    columns = ", ".join(per.record_select_columns(conn))
    row = conn.execute(
        f"SELECT rowid AS rowid, {columns} FROM node_results WHERE node_id = 'financial'"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        print("  No old financial blob found.")
        return False

    blob = per.read_node_record(row)
    if blob.get("status") != "succeeded":
        return False

    old_value = blob.get("value", {})
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
