"""DB-level migration contract — against a synthesized copy of the real
node_results schema, not just the pure transform.

Pins what the script must guarantee on the 862k-row live DB:
the re-key preserves code_version/created_at (no startup replan), the
money stays under the new id keys, numeric ids are assigned once with
max+1 continuation, and an applied migration is idempotent.
"""

from __future__ import annotations

import json
import sqlite3
import zlib

from scripts.backfill_person_ids import apply_migration, collect_mapping, rows_to_remap

SCHEMA = """
CREATE TABLE node_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT,
    result_json BLOB,
    dep_timestamps TEXT,
    created_at TEXT,
    code_version TEXT
);
"""

_CREATED = "2026-01-01T00:00:00"
_VERSION = "v9"


def _row(conn, node_id: str, payload, dep=None, created: str = _CREATED, version: str = _VERSION) -> None:
    conn.execute(
        "INSERT INTO node_results (node_id, result_json, dep_timestamps, created_at, code_version) VALUES (?,?,?,?,?)",
        (node_id, zlib.compress(json.dumps(payload).encode()), json.dumps(dep or {}), created, version),
    )


def _seed(conn) -> None:
    persons_payload = {
        "status": "succeeded",
        "value": [
            {"name": "Simon", "has_car": True},
            {"name": "Lorena", "has_car": False},
            {"name": "Ashby", "has_car": True},
            {"name": "George", "is_child": True},
        ],
    }
    _row(conn, "persons", persons_payload)
    _row(conn, "111/Simon/Pimlico/walk", {"status": "succeeded", "value": {"amount": "0", "currency": "GBP"}},
         dep={"111/Simon/Pimlico/poi": "2026-01-01"}, created="2026-01-01T00:00:01")
    _row(conn, "111/Simon/Pimlico/final_fuel", {"status": "succeeded", "value": {"amount": "5", "currency": "GBP"}},
         dep={"111/Simon/Pimlico/walk": "2026-01-01"}, created="2026-01-01T00:00:02")
    _row(conn, "111/works_estimates",
         {"status": "succeeded", "value": {"Ashby": {"amount": "25000.00", "currency": "GBP"}}},
         created="2026-01-01T00:00:03")
    _row(conn, "111/Lorena/Lorena Square/walk", {"status": "succeeded", "value": "x"},
         created="2026-01-01T00:00:04")
    _row(conn, "9999/best_address", {"status": "succeeded", "value": "1 Test St"}, created="2026-01-01T00:00:05")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _apply(conn):
    """Drive the REAL migration harness (apply_migration), not a replica."""
    persons = conn.execute(
        "SELECT id, result_json FROM node_results WHERE node_id='persons' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    data = json.loads(zlib.decompress(persons["result_json"]).decode())
    mapping = collect_mapping(data["value"])
    remaps = rows_to_remap(conn, mapping)
    ok = apply_migration(conn, "unused.db", persons["id"], data, mapping, remaps, use_backup=False, verify=False)
    assert ok
    return mapping, remaps


def test_apply_rekeys_ids_timestamps_and_money():
    conn = _connect()
    _seed(conn)
    mapping, remaps = _apply(conn)

    assert mapping == {"Simon": "1", "Lorena": "2", "Ashby": "3", "George": "4"}
    rows = {r["node_id"]: r for r in conn.execute(
        "SELECT node_id, dep_timestamps, created_at, code_version, result_json FROM node_results")}

    walk = rows["111/1/Pimlico/walk"]
    assert json.loads(walk["dep_timestamps"]) == {"111/1/Pimlico/poi": "2026-01-01"}
    # the no-replan guarantee: clocks and code version survive the re-key
    assert walk["created_at"] == "2026-01-01T00:00:01" and walk["code_version"] == _VERSION

    works = json.loads(zlib.decompress(rows["111/works_estimates"]["result_json"]).decode())
    assert works["value"]["3"]["amount"] == "25000.00"
    assert "Ashby" not in works["value"]

    # a label that contains a person name: only the leading segment re-keys
    assert "111/2/Lorena Square/walk" in rows
    assert "9999/best_address" in rows
    assert len(remaps) == 4  # walk, final_fuel, works (money), label-containment row — best_address is a true no-op


def test_apply_is_idempotent_and_preserves_existing_ids():
    conn = _connect()
    _seed(conn)
    # a person who already has a numeric id — the continuation must pass it
    conn.execute(
        "UPDATE node_results SET result_json=? WHERE node_id='persons'",
        (zlib.compress(json.dumps({"status": "succeeded", "value": [
            {"name": "Simon"}, {"name": "Lorena"}, {"name": "Ashby"}, {"name": "George"},
            {"name": "Zed", "person_id": "7"},
        ]}).encode()),),
    )

    mapping, _ = _apply(conn)
    # max(existing)=7 → the missing four continue at 8..; 7 stays taken
    assert mapping["Zed"] == "7"
    assert {mapping[n] for n in ("Simon", "Lorena", "Ashby", "George")} == {"8", "9", "10", "11"}

    data = json.loads(zlib.decompress(conn.execute(
        "SELECT result_json FROM node_results WHERE node_id='persons'").fetchone()[0]).decode())
    assert {p["name"]: p["person_id"] for p in data["value"]} == mapping

    second = rows_to_remap(conn, collect_mapping(data["value"]))
    assert second == [], "a second pass must find zero remappable rows"


def test_apply_advances_source_freshness_only():
    """The data-staleness contract: rows the migration REWROTE as inputs
    (persons, person-keyed works) must carry a NEW created_at so the
    boot sweep's dep-timestamp check sees consumers as stale; re-keyed
    DERIVED rows keep their clock — their values did not change, and
    planners must not be marked."""
    conn = _connect()
    _seed(conn)
    _, _ = _apply(conn)

    rows = {r["node_id"]: r for r in conn.execute(
        "SELECT node_id, created_at FROM node_results")}

    persons_ts = rows["persons"]["created_at"]
    works_ts = rows["111/works_estimates"]["created_at"]
    assert persons_ts > "2026-01-01T00:00:00", "the persons source must be stamped newer"
    assert works_ts > "2026-01-01T00:00:00", "the rewritten works source must be stamped newer"
    # derived rows keep their original clocks (no fake staleness on unchanged values)
    assert rows["111/1/Pimlico/walk"]["created_at"] == "2026-01-01T00:00:01"
    assert rows["111/works_estimates"]["created_at"] == works_ts


def test_apply_bumps_only_the_current_persons_row():
    """Append-order must survive: the persons freshness bump targets the
    CURRENT row by id only. Stamping every history version identically
    makes the latest-row query arbitrary — an old row can win the tie and
    the household silently shrink (the clean-room finding)."""
    conn = _connect()
    _seed(conn)
    # a legacy 3-person history row, older than the current 4-person row
    legacy = {"status": "succeeded", "value": [
        {"name": "Simon"}, {"name": "Lorena"}, {"name": "George"},
    ]}
    _row(conn, "persons", legacy, created="2025-01-01T00:00:00")

    _, _ = _apply(conn)

    rows = sorted(
        conn.execute(
            "SELECT id, created_at, result_json FROM node_results"
            " WHERE node_id='persons' ORDER BY created_at, id"
        ).fetchall(),
        key=lambda r: (r["created_at"], r["id"]),
    )
    assert len(rows) == 2
    legacy_row, current_row = rows
    # the old version keeps its clock; only the current row advanced
    assert legacy_row["created_at"] == "2025-01-01T00:00:00"
    assert current_row["created_at"] > "2026-01-01T00:00:00"
    current = json.loads(zlib.decompress(current_row["result_json"]).decode())["value"]
    assert {p["name"]: p["person_id"] for p in current} == {"Simon": "1", "Lorena": "2", "Ashby": "3", "George": "4"}
    # the script's own reader must resolve the SAME current row
    read_id = conn.execute(
        "SELECT id FROM node_results WHERE node_id='persons'"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()["id"]
    assert read_id == current_row["id"]
    current_names = [p["name"] for p in current]
    assert current_names == ["Simon", "Lorena", "Ashby", "George"]

