"""DB-level migration contract — against a synthesized copy of the real
node_results schema, not just the pure transform.

Two shapes are exercised: the pre-split schema (one zlib'd ``result_json``
per row, what the live 862k-row DB still has) and the split schema a fresh
``dag.persistence.init_db()`` creates (per-field columns, no ``result_json``
and — like init_db — no ``id`` column, so rows are keyed by ``rowid``).

Pins what the script must guarantee on the live DB: the re-key preserves
code_version/created_at (no startup replan), the money stays under the new
id keys, numeric ids are assigned once with max+1 continuation, and an
applied migration is idempotent.
"""

from __future__ import annotations

import json
import sqlite3
import zlib

import pytest

from dag import persistence
from scripts.backfill_person_ids import (
    _read_persons,
    apply_migration,
    collect_mapping,
    rows_to_remap,
)

LEGACY_SCHEMA = """
CREATE TABLE node_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT,
    result_json BLOB,
    dep_timestamps TEXT,
    created_at TEXT,
    code_version TEXT
);
"""

# The real deploy shape: a pre-split database that ``init_db()`` has already
# ALTERed, so the rows still live in ``result_json`` while the split columns
# exist and are NULL — the backfill runs BEFORE scripts/split_node_results.py,
# so it must keep both in step or the split would undo the re-key.
MID_SCHEMA = """
CREATE TABLE node_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT,
    result_json BLOB,
    status TEXT,
    value_json TEXT,
    error TEXT,
    error_detail_json TEXT,
    source_url TEXT,
    source_label TEXT,
    provenance_z BLOB,
    extra_json TEXT,
    dep_timestamps TEXT,
    created_at TEXT,
    code_version TEXT
);
"""

# Mirrors dag.persistence.init_db(): per-field columns instead of result_json,
# and no ``id`` column — a fresh database only has ``rowid``.
SPLIT_SCHEMA = """
CREATE TABLE node_results (
    node_id TEXT NOT NULL,
    status TEXT,
    value_json TEXT,
    error TEXT,
    error_detail_json TEXT,
    source_url TEXT,
    source_label TEXT,
    provenance_z BLOB,
    extra_json TEXT,
    dep_timestamps TEXT,
    created_at TEXT NOT NULL,
    code_version TEXT
);
"""

SHAPES = ["legacy", "mid", "split"]

_CREATED = "2026-01-01T00:00:00"
_VERSION = "v9"


def _columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(row[1] for row in conn.execute("PRAGMA table_info(node_results)"))


def _insert(conn, node_id: str, payload, dep=None, created: str = _CREATED, version: str = _VERSION) -> None:
    """Insert one row in whichever shape this connection has."""
    if "result_json" in _columns(conn):
        conn.execute(
            "INSERT INTO node_results (node_id, result_json, dep_timestamps, created_at, code_version)"
            " VALUES (?,?,?,?,?)",
            (node_id, zlib.compress(json.dumps(payload).encode()), json.dumps(dep or {}), created, version),
        )
        return
    values = {
        "node_id": node_id,
        **persistence.record_columns(payload),
        "dep_timestamps": json.dumps(dep or {}),
        "created_at": created,
        "code_version": version,
    }
    columns = [column for column in _columns(conn) if column in values]
    conn.execute(
        f"INSERT INTO node_results ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        tuple(values[column] for column in columns),
    )


def _records(conn) -> dict[str, dict]:
    """Every row as ``read_node_record`` decodes it, keyed by node id, with the
    row's own stamps attached for the no-replan assertions."""
    selected = ", ".join(
        (
            "node_id",
            "created_at",
            "code_version",
            "dep_timestamps",
            *persistence.record_select_columns(conn),
        )
    )
    out: dict[str, dict] = {}
    for row in conn.execute(f"SELECT {selected} FROM node_results"):
        record = persistence.read_node_record(row)
        record["_created_at"] = row["created_at"]
        record["_code_version"] = row["code_version"]
        record["_dep_timestamps"] = json.loads(row["dep_timestamps"] or "{}")
        out[row["node_id"]] = record
    return out


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
    _insert(conn, "persons", persons_payload)
    _insert(
        conn,
        "111/Simon/Pimlico/walk",
        {"status": "succeeded", "value": {"amount": "0", "currency": "GBP"}},
        dep={"111/Simon/Pimlico/poi": "2026-01-01"},
        created="2026-01-01T00:00:01",
    )
    _insert(
        conn,
        "111/Simon/Pimlico/final_fuel",
        {"status": "succeeded", "value": {"amount": "5", "currency": "GBP"}},
        dep={"111/Simon/Pimlico/walk": "2026-01-01"},
        created="2026-01-01T00:00:02",
    )
    _insert(
        conn,
        "111/works_estimates",
        {"status": "succeeded", "value": {"Ashby": {"amount": "25000.00", "currency": "GBP"}}},
        created="2026-01-01T00:00:03",
    )
    _insert(
        conn,
        "111/Lorena/Lorena Square/walk",
        {"status": "succeeded", "value": "x"},
        created="2026-01-01T00:00:04",
    )
    _insert(conn, "9999/best_address", {"status": "succeeded", "value": "1 Test St"}, created="2026-01-01T00:00:05")


_SCHEMAS = {"legacy": LEGACY_SCHEMA, "mid": MID_SCHEMA, "split": SPLIT_SCHEMA}


def _connect(shape: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMAS[shape])
    return conn


def _persons(conn):
    """The latest persons row, asserting the seed wrote one."""
    found = _read_persons(conn)
    assert found is not None, "seed must have a persons row"
    return found


def _apply(conn):
    """Drive the REAL migration harness (apply_migration), not a replica."""
    persons_id, data = _persons(conn)
    mapping = collect_mapping(data["value"])
    # the stream is lazy — count it BEFORE the apply mutates the table
    remap_count = sum(1 for _ in rows_to_remap(conn, mapping))
    result = apply_migration(
        conn, persons_id, data, mapping, rows_to_remap(conn, mapping), backup_path=None, verify=False
    )
    assert result.ok
    return mapping, remap_count


@pytest.mark.parametrize("shape", SHAPES)
def test_apply_rekeys_ids_timestamps_and_money(shape):
    conn = _connect(shape)
    _seed(conn)
    mapping, remap_count = _apply(conn)

    assert mapping == {"Simon": "1", "Lorena": "2", "Ashby": "3", "George": "4"}
    rows = _records(conn)

    walk = rows["111/1/Pimlico/walk"]
    assert walk["_dep_timestamps"] == {"111/1/Pimlico/poi": "2026-01-01"}
    # the no-replan guarantee: clocks and code version survive the re-key
    assert walk["_created_at"] == "2026-01-01T00:00:01" and walk["_code_version"] == _VERSION

    works = rows["111/works_estimates"]
    assert works["value"]["3"]["amount"] == "25000.00"
    assert "Ashby" not in works["value"]

    # a label that contains a person name: only the leading segment re-keys
    assert "111/2/Lorena Square/walk" in rows
    assert "9999/best_address" in rows
    # walk, final_fuel, works (money), label-containment row — best_address is a true no-op
    assert remap_count == 4


@pytest.mark.parametrize("shape", SHAPES)
def test_apply_is_idempotent_and_preserves_existing_ids(shape):
    conn = _connect(shape)
    _seed(conn)
    # a person who already has a numeric id — the continuation must pass it
    persons_id, _ = _persons(conn)
    persistence.write_node_record(
        conn,
        persons_id,
        {
            "status": "succeeded",
            "value": [
                {"name": "Simon"},
                {"name": "Lorena"},
                {"name": "Ashby"},
                {"name": "George"},
                {"name": "Zed", "person_id": "7"},
            ],
        },
    )

    mapping, _ = _apply(conn)
    # max(existing)=7 → the missing four continue at 8..; 7 stays taken
    assert mapping["Zed"] == "7"
    assert {mapping[n] for n in ("Simon", "Lorena", "Ashby", "George")} == {"8", "9", "10", "11"}

    data = _persons(conn)[1]
    assert {p["name"]: p["person_id"] for p in data["value"]} == mapping

    second = rows_to_remap(conn, collect_mapping(data["value"]))
    assert list(second) == [], "a second pass must find zero remappable rows"


@pytest.mark.parametrize("shape", SHAPES)
def test_apply_advances_source_freshness_only(shape):
    """The data-staleness contract: rows the migration REWROTE as inputs
    (persons, person-keyed works) must carry a NEW created_at so the
    boot sweep's dep-timestamp check sees consumers as stale; re-keyed
    DERIVED rows keep their clock — their values did not change, and
    planners must not be marked."""
    conn = _connect(shape)
    _seed(conn)
    _, _ = _apply(conn)

    rows = _records(conn)

    persons_ts = rows["persons"]["_created_at"]
    works_ts = rows["111/works_estimates"]["_created_at"]
    assert persons_ts > "2026-01-01T00:00:00", "the persons source must be stamped newer"
    assert works_ts > "2026-01-01T00:00:00", "the rewritten works source must be stamped newer"
    # derived rows keep their original clocks (no fake staleness on unchanged values)
    assert rows["111/1/Pimlico/walk"]["_created_at"] == "2026-01-01T00:00:01"


@pytest.mark.parametrize("shape", SHAPES)
def test_apply_bumps_only_the_current_persons_row(shape):
    """Append-order must survive: the persons freshness bump targets the
    CURRENT row only. Stamping every history version identically makes the
    latest-row query arbitrary — an old row can win the tie and the household
    silently shrink (the clean-room finding)."""
    conn = _connect(shape)
    _seed(conn)
    # a legacy 3-person history row, older than the current 4-person row
    legacy = {
        "status": "succeeded",
        "value": [{"name": "Simon"}, {"name": "Lorena"}, {"name": "George"}],
    }
    _insert(conn, "persons", legacy, created="2025-01-01T00:00:00")

    _, _ = _apply(conn)

    selected = ", ".join(("rowid AS rowid", "created_at", *persistence.record_select_columns(conn)))
    rows = sorted(
        conn.execute(
            f"SELECT {selected} FROM node_results WHERE node_id='persons' ORDER BY created_at, rowid"
        ).fetchall(),
        key=lambda r: (r["created_at"], r["rowid"]),
    )
    assert len(rows) == 2
    legacy_row, current_row = rows
    assert legacy_row["created_at"] == "2025-01-01T00:00:00"
    assert current_row["created_at"] > "2026-01-01T00:00:00"
    current = persistence.read_node_record(current_row)["value"]
    assert {p["name"]: p["person_id"] for p in current} == {
        "Simon": "1",
        "Lorena": "2",
        "Ashby": "3",
        "George": "4",
    }
    # the script's own reader must resolve the SAME current row
    read_id, _ = _persons(conn)
    assert read_id == current_row["rowid"]
    current_names = [p["name"] for p in current]
    assert current_names == ["Simon", "Lorena", "Ashby", "George"]


@pytest.mark.parametrize("shape", SHAPES)
def test_apply_reports_the_real_applied_count(shape):
    """The 2026-09-23 regression: after the apply, a post-apply scan on the
    same connection sees migrated rows and reports zero — the run printed
    'rows remapped: 0 (applied)' while rewrites had happened. The count
    must come from the apply generator's own consumption."""

    conn = _connect(shape)
    _seed(conn)
    found = _read_persons(conn)
    assert found is not None, "seed must have a persons row"
    persons_id, data = found
    value = data.get("value") or []
    mapping = collect_mapping(value)
    assert mapping, "seed must have persons"
    result = apply_migration(
        conn,
        persons_id,
        data,
        mapping,
        rows_to_remap(conn, mapping),
        backup_path=None,
        verify=True,
    )
    assert result.ok
    assert result.applied > 0, "the apply generator itself must report rows rewritten"


def test_corrupt_works_estimates_blob_aborts_the_scan():
    """An unreadable works_estimates payload must abort the migration rather
    than be skipped — the fail-fast the original blob reader had."""
    conn = _connect("legacy")
    conn.execute(
        "INSERT INTO node_results (node_id, result_json, dep_timestamps, created_at, code_version)"
        " VALUES ('111/works_estimates', ?, '{}', '2026-01-01T00:00:00', 'v9')",
        (b"\x78\x9cnot-a-zlib-stream",),
    )
    with pytest.raises(RuntimeError, match="not parseable"):
        list(rows_to_remap(conn, {"Ashby": "3"}))


def test_mid_shape_keeps_the_legacy_blob_in_sync():
    """On the shape the deploy actually hits (result_json still present, split
    columns already ALTERed in), scripts/split_node_results.py runs AFTER this
    migration and converts the blob. A re-key written only into the split
    columns would be undone there — the legacy blob must carry it too."""
    conn = _connect("mid")
    _seed(conn)
    _apply(conn)

    works_blob = conn.execute(
        "SELECT result_json FROM node_results WHERE node_id='111/works_estimates'"
    ).fetchone()["result_json"]
    assert list(json.loads(zlib.decompress(works_blob).decode())["value"]) == ["3"]

    persons_blob = conn.execute(
        "SELECT result_json FROM node_results WHERE node_id='persons'"
    ).fetchone()["result_json"]
    people = json.loads(zlib.decompress(persons_blob).decode())["value"]
    assert {p["name"]: p["person_id"] for p in people} == {
        "Simon": "1",
        "Lorena": "2",
        "Ashby": "3",
        "George": "4",
    }
