"""Generic persistence layer for DAG nodes.

Stores versioned source values and resolved derived values in SQLite.
Serialises complex types via TypeAdapter with ``_type``/``_module`` markers.
"""

from __future__ import annotations

import importlib
import json
import logging
import sqlite3
import threading
import zlib
from datetime import UTC, datetime
from decimal import Decimal as _Decimal
from enum import Enum
from pathlib import Path
from typing import Any, cast, override

from money import Money as _Money
from pint import Quantity
from pydantic import TypeAdapter

logger = logging.getLogger(__name__)


DB_PATH: Path | None = None
testing: bool = False
_connection_cache = threading.local()


class DagJSONEncoder(json.JSONEncoder):
    """Handles enums, Decimal, Money, Quantity, and other non-serializable types in DAG node results."""

    @override
    def default(self, o):
        if isinstance(o, Enum):
            return o.name.lower()
        if isinstance(o, _Decimal):
            return float(o)
        if isinstance(o, _Money):
            # lucidlint: ignore record-shape wire-format dict — serialization boundary
            return {"amount": str(o.amount), "currency": o.currency}
        if isinstance(o, cast(type, Quantity)):
            m = float(o.magnitude)
            # lucidlint: ignore record-shape wire-format dict — serialization boundary
            return {"value": int(m) if m == int(m) else m, "unit": str(o.units)}
        return super().default(o)


_ZLIB_LEVEL = 6
"""zlib level for the provenance blob.  Level 6 vs 9 trades ~2% ratio for
~4x faster compress — the money-cascade provenance trees (the bulk of a
row's bytes) compress ~25x either way."""

PROVENANCE_KEY = "provenance"
"""The one record field that keeps compression.

Every other part of a node record is plain text in its own column, so the hot
readers (attempt load, staleness checks, the listing) parse only what they use
and never inflate a provenance tree — those trees are the bulk of the table
(1.53 GB live, mostly the money cascades' expansions) and only the provenance
endpoint reads them.
"""

_SPLIT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("status", "TEXT"),
    ("value_json", "TEXT"),
    ("error", "TEXT"),
    ("error_detail_json", "TEXT"),
    ("source_url", "TEXT"),
    ("source_label", "TEXT"),
    ("provenance_z", "BLOB"),
    ("extra_json", "TEXT"),
)
"""The per-field columns that replace the single ``result_json`` blob.

``source_label`` is its own column because every user-input load reads it
(``_load_persisted_label``); ``extra_json`` is the catch-all for any other
node-specific record field, so the split can never drop one.
"""

_PLAIN_COLUMN_KEYS: tuple[str, ...] = ("status", "error", "source_url", "source_label")
"""Record fields stored verbatim as their own TEXT column."""

_columns_cache: dict[str, tuple[str, ...]] = {}
_split_ensured: set[str] = set()

def _reset_caches() -> None:
    """Drop the per-database schema caches (test isolation).

    A test can recreate a database under the same path with a different
    schema (a pre-split table to prove legacy rows still read); the caches
    are keyed by path, so they must be dropped with it.
    """
    _columns_cache.clear()
    _split_ensured.clear()


def compress_result(text: str) -> bytes:
    """zlib-compress a payload (the provenance blob) for storage."""
    return zlib.compress(text.encode("utf-8"), _ZLIB_LEVEL)


def decompress_result(raw: str | bytes) -> str:
    if isinstance(raw, bytes) and raw[:1] == b"\x78":
        return zlib.decompress(raw).decode("utf-8")
    if isinstance(raw, bytes):
        # Degenerate: a BLOB that is not a zlib stream.  JSON text never
        # starts with 0x78, so this only happens with a foreign write.
        return raw.decode("utf-8")
    return raw


def _record_columns() -> tuple[str, ...]:
    """The columns this database's node_results actually has (cached).

    A migrated or fresh database has the split columns and no ``result_json``;
    an older one still carries the legacy blob column (NOT NULL), so writes
    mirror the provenance-free record there until the shipped migration
    converts it.
    """
    key = str(DB_PATH)
    cached = _columns_cache.get(key)
    if cached is None:
        cached = tuple(r[1] for r in _get_db().execute("PRAGMA table_info(node_results)"))
        _columns_cache[key] = cached
    return cached


def _ensure_split_columns() -> None:
    """Idempotently add the split columns to an older node_results.

    Cheap ALTERs, cached per DB path — the same pattern as the code_version
    column.  The one-time conversion (and the drop of the legacy blob) is the
    shipped migration's job: scripts/split_node_results.py.
    """
    key = str(DB_PATH)
    if key in _split_ensured:
        return
    conn = _get_db()
    existing = {r[1] for r in conn.execute("PRAGMA table_info(node_results)")}
    for column, column_type in _SPLIT_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE node_results ADD COLUMN {column} {column_type}")
    conn.commit()
    _split_ensured.add(key)
    _columns_cache.pop(key, None)


def _split_values(record: dict[str, Any]) -> dict[str, Any]:
    """The record's fields as storage column values — provenance compressed."""
    provenance = record.get(PROVENANCE_KEY)
    mapped = {*_PLAIN_COLUMN_KEYS, "value", "error_detail", PROVENANCE_KEY}
    extra = {k: v for k, v in record.items() if k not in mapped}
    values: dict[str, Any] = {
        "value_json": json.dumps(record["value"], cls=DagJSONEncoder) if "value" in record else None,
        "error_detail_json": (
            json.dumps(record["error_detail"], cls=DagJSONEncoder)
            if record.get("error_detail") is not None
            else None
        ),
        "provenance_z": (
            compress_result(json.dumps(provenance, cls=DagJSONEncoder)) if provenance is not None else None
        ),
        "extra_json": json.dumps(extra, cls=DagJSONEncoder) if extra else None,
        # Legacy databases keep a NOT NULL result_json: mirror the record
        # WITHOUT provenance there (small, and enough for a rollback to read).
        "result_json": json.dumps(
            {k: v for k, v in record.items() if k != PROVENANCE_KEY}, cls=DagJSONEncoder
        ),
    }
    for key in _PLAIN_COLUMN_KEYS:
        values[key] = record.get(key)
    return values


def _read_legacy_record(raw: str | bytes) -> dict[str, Any]:
    """Parse a pre-split ``result_json`` value (whole-row zlib or plain JSON)."""
    return cast(dict[str, Any], json.loads(decompress_result(raw)))


def _record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    """A row's record from the split columns (legacy blob for pre-split rows).

    Never inflates provenance — that is ``latest_node_provenance``'s job.
    """
    keys = row.keys()
    if "status" not in keys or (row["status"] is None and "result_json" in keys and row["result_json"]):
        legacy = _read_legacy_record(row["result_json"])
        legacy.pop(PROVENANCE_KEY, None)
        return legacy
    status = row["status"]
    keys = row.keys()
    record: dict[str, Any] = {
        "status": status,
        # The status vocabulary is exactly succeeded/pending/impossible, so
        # the flags are derivable — they were carried, not computed.
        "succeeded": status == "succeeded",
        "pending": status == "pending",
        "impossible": status == "impossible",
    }
    if row["value_json"] is not None:
        record["value"] = json.loads(row["value_json"])
    if row["error"] is not None:
        record["error"] = row["error"]
    if row["error_detail_json"] is not None:
        record["error_detail"] = json.loads(row["error_detail_json"])
    for column in ("source_url", "source_label"):
        if column in keys and row[column] is not None:
            record[column] = row[column]
    if "extra_json" in keys and row["extra_json"] is not None:
        extra = json.loads(row["extra_json"])
        if isinstance(extra, dict):
            record.update(extra)
    return record


def _get_db() -> sqlite3.Connection:
    global DB_PATH
    if DB_PATH is None:
        DB_PATH = Path("data/houses.db")

    if hasattr(_connection_cache, "conn"):
        return _connection_cache.conn

    if testing:
        raise RuntimeError(
            f"Refusing to open production DB at {DB_PATH} — test fixture "
            "should have replaced _get_db with an in-memory connection. "
            "Did a direct import bypass the replacement?"
        )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _connection_cache.conn = conn
    return conn


def close_db() -> None:
    """Close the cached database connection and clear it."""
    if hasattr(_connection_cache, "conn"):
        _connection_cache.conn.close()
        _connection_cache.conn = None


# lucidlint: ignore unused deliberate test seam — round-trip primitives driven directly by test_persistence.py;
def _serialize_value(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return json.dumps(val)
    if isinstance(val, str):
        if not val:
            return ""
        return json.dumps(val)
    if isinstance(val, (int, float)):
        return str(val)
    try:
        ta = TypeAdapter(type(val))
        d = ta.dump_python(val)
        if isinstance(d, dict):
            d["_type"] = type(val).__name__
            d["_module"] = type(val).__module__
        return json.dumps(d)
    # lucidlint: ignore broad-except serialization failure logs the type then re-raises — never persists silently
    except Exception:
        logger.exception("Failed to serialize %s", type(val).__name__)
        raise


# lucidlint: ignore unused deliberate test seam — round-trip primitives driven directly by test_persistence.py;
def _deserialize_value(raw: str | None) -> Any:
    if raw is None:  # was: if not raw — empty string "" should not be treated as None
        return None
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(d, dict) and "_type" in d and "_module" in d:
        try:
            mod = importlib.import_module(d["_module"])
            cls = getattr(mod, d["_type"])
            fields = {k: v for k, v in d.items() if not k.startswith("_")}
            return cls(**fields)
        # lucidlint: ignore broad-except deserialization failure logs the _type then re-raises
        except Exception:
            logger.exception("Failed to deserialize %s", d.get("_type", "unknown"))
            raise
    return d


_code_version_ensured: set[str] = set()


def _ensure_code_version_column() -> None:
    """Idempotently add the ``code_version`` column to node_results.

    Older databases predate the code-version stamp; ALTER is cheap and
    safe (SQLite), and rows written before the column exists get NULL,
    which the staleness check treats as "unknown code" → one recompute.
    Cached per DB path — the PRAGMA was running on EVERY persist/load
    (the hottest persistence path) before the review caught it.
    """
    key = str(DB_PATH)
    if key in _code_version_ensured:
        return
    conn = _get_db()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(node_results)")]
    if "code_version" not in cols:
        conn.execute("ALTER TABLE node_results ADD COLUMN code_version TEXT")
        conn.commit()
    _code_version_ensured.add(key)


def init_db(db_path: str | None = None) -> None:
    """Initialise the SQLite database schema, migrating older databases."""
    global DB_PATH
    if db_path:
        DB_PATH = Path(db_path)
    conn = _get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS node_results (
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
        )
        """
    )
    # Latest-row lookups (latest_node_result, property_created_at) are the
    # hot path — the index was dropped in the code_version rewrite and a
    # fresh database must still get it.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nr_node ON node_results(node_id, created_at DESC);")
    conn.commit()
    _ensure_code_version_column()
    # Older databases still carry the single result_json blob: add the split
    # columns (cheap ALTERs) so the new write/read shape works before the
    # shipped migration converts the rows and drops the blob.
    _ensure_split_columns()


# lucidlint: ignore record-shape wire-format dict — serialization boundary
def save_node_result(
    node_id: str,
    result_dict: dict[str, Any],
    dep_timestamps: dict[str, str] | None = None,
    created_at: str | None = None,
    code_version: str | None = None,
) -> int:
    """Persist a node's to_json() output to the node_results table.

    Each call appends a new row. The most recent row is the current value.
    *created_at* should be the same value used by the caller's ``_db_created_at``
    so that the in-memory timestamp matches the DB column (avoids false staleness).
    When omitted, a fresh timestamp is generated (callers that don't care about
    round-trip consistency).  *code_version* fingerprints the compute code
    that produced the value — a persisted row whose version no longer matches
    the current compute is stale-in-code and must recompute.

    Single-writer enforcement: persistence runs on the DAG processor
    thread (or a single-threaded context — startup, tests, scripts);
    see docs/dag-library.md → 'Thread rules'.
    """
    # lucidlint: ignore inline-import cycle break — scheduler imports this module's writers at top
    from dag.scheduler import assert_mutation_allowed

    assert_mutation_allowed()
    if not _table_exists("node_results"):
        init_db()
    _ensure_code_version_column()
    _ensure_split_columns()
    conn = _get_db()
    now = created_at or datetime.now(UTC).isoformat()
    values = {
        "node_id": node_id,
        **_split_values(result_dict),
        "dep_timestamps": json.dumps(dep_timestamps, cls=DagJSONEncoder) if dep_timestamps else None,
        "created_at": now,
        "code_version": code_version,
    }
    columns = [c for c in _record_columns() if c in values]
    cur = conn.execute(
        f"INSERT INTO node_results ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        tuple(values[c] for c in columns),
    )
    conn.commit()
    rowid = cur.lastrowid
    return rowid if rowid is not None else 0


# lucidlint: ignore record-shape wire-format dict — serialization boundary
def latest_node_result(node_id: str) -> dict[str, Any] | None:
    """Return the most recent to_json() dict for a node, or None.

    Reads never block on writes and never need to flush: this reads
    committed DB state as-is (WAL snapshot; timestamp predicates exclude
    unwritten rows by construction — docs/dag-library.md → 'Thread
    rules').
    """
    return _fetch_latest_row(node_id)


# lucidlint: ignore record-shape wire-format dict — the stored node to_json() payload, serialization boundary (keys
# vary per node type; the _-prefixed metadata is added here, never in the node) (coding-standards.md)
def _fetch_latest_row(node_id: str, before: str | None = None) -> dict[str, Any] | None:
    """The row fetch shared by latest_node_result and node_result_before:
    read committed state as-is (WAL snapshot; the optional ``before``
    timestamp excludes unwritten rows by construction — thread rules)."""
    if not _table_exists("node_results"):
        init_db()
        return None
    _ensure_code_version_column()
    _ensure_split_columns()
    conn = _get_db()
    # provenance_z is deliberately NOT selected: its blob is the bulk of the
    # row and only latest_node_provenance inflates it.
    wanted = (
        *[c for c, _ in _SPLIT_COLUMNS if c != "provenance_z"],
        # Pre-split databases only: the fallback for rows without columns.
        "result_json",
        "dep_timestamps",
        "created_at",
        "code_version",
    )
    selected = ", ".join(c for c in wanted if c in _record_columns())
    if before is None:
        row = conn.execute(
            f"SELECT {selected} FROM node_results"
            " WHERE node_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (node_id,),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT {selected} FROM node_results"
            " WHERE node_id=? AND created_at < ? ORDER BY created_at DESC LIMIT 1",
            (node_id, before),
        ).fetchone()
    if row is None:
        return None
    result = _record_from_row(row)
    result["_dep_timestamps"] = json.loads(row["dep_timestamps"]) if row["dep_timestamps"] else {}
    result["_persisted_at"] = row["created_at"]
    result["_code_version"] = row["code_version"]
    return result


def latest_node_provenance(node_id: str) -> dict[str, Any] | None:
    """The node's recorded provenance tree, or None.

    The ONLY read that inflates the compressed provenance blob — every other
    reader skips the column entirely, which is the point of the split: the
    trees are the bulk of the table and only the provenance endpoint wants
    them.
    """
    if not _table_exists("node_results"):
        init_db()
        return None
    _ensure_code_version_column()
    _ensure_split_columns()
    conn = _get_db()
    columns = _record_columns()
    wanted = [c for c in ("provenance_z", "result_json") if c in columns]
    if not wanted:
        return None
    row = conn.execute(
        f"SELECT {', '.join(wanted)} FROM node_results"
        " WHERE node_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (node_id,),
    ).fetchone()
    if row is None:
        return None
    keys = row.keys()
    if "provenance_z" in keys and row["provenance_z"] is not None:
        return cast(dict[str, Any], json.loads(decompress_result(row["provenance_z"])))
    if "result_json" in keys and row["result_json"]:
        # A pre-split row kept its tree inside the legacy blob.
        legacy = _read_legacy_record(row["result_json"]).get(PROVENANCE_KEY)
        return legacy if isinstance(legacy, dict) else None
    return None


# lucidlint: ignore record-shape wire-format dict — serialization boundary (same stored node payload as
# latest_node_result, read strictly-before a timestamp) (coding-standards.md)
def node_result_before(node_id: str, before: str) -> dict[str, Any] | None:
    """Return the most recent to_json() dict for a node STRICTLY BEFORE
    the ISO-8601 timestamp *before*, or None.

    node_results is append-only history ("each call appends a new row"),
    so this is a reference into the DAG's own past — e.g. the what-if
    restore reads the persons attempt from before the scenario started.
    Reads never block on writes — see latest_node_result.
    """
    return _fetch_latest_row(node_id, before)


def _table_exists(name: str) -> bool:
    conn = _get_db()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def property_created_at(rid: str) -> str | None:
    """Return the ISO-8601 timestamp of the earliest node_result for a property.

    This is when the property was first added (the first UserInputNode push).
    Returns None if no node_results exist for this RID.
    """
    if not _table_exists("node_results"):
        return None
    conn = _get_db()
    row = conn.execute(
        # GLOB, not LIKE: LIKE is case-insensitive, so SQLite cannot use
        # idx_nr_node's node_id prefix for the pattern and scans the whole
        # 1M+ row table per property — measured 127ms x 46 properties on
        # every /api/properties/all load. GLOB is case-sensitive and its
        # prefix optimization hits the index: 2.7ms.
        "SELECT MIN(created_at) FROM node_results WHERE node_id GLOB ?",
        (f"{rid}/*",),
    ).fetchone()
    return row[0] if row and row[0] else None


def delete_node_results_for_rid(rid: str) -> None:
    """Remove every persisted row for a property (user-removed)."""
    # rid is user-supplied path input — escape LIKE wildcards so a
    # '%'/'_' cannot turn this into a delete of unrelated properties'
    # rows (PR #68 review, High).
    escaped = rid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    conn = _get_db()
    conn.execute(
        "DELETE FROM node_results WHERE node_id LIKE ? ESCAPE '\\'",
        (f"{escaped}/%",),
    )
    conn.commit()


#: Node-id namespaces that are not properties.  The global settings nodes
#: live under ``settings/<name>``; the slash-less sources (``persons``,
#: ``financial``, …) never reach this query.
NON_PROPERTY_NAMESPACES = frozenset({"settings"})


def property_rids() -> list[str]:
    """Every node-id prefix that could be a property RID.

    The global settings namespace is excluded; nothing else is.  Filtering
    for numeric prefixes here made the startup loader's pollution guard
    unreachable — a row under a test-data id was silently ignored instead of
    failing loudly, which is the opposite of what that guard exists for.
    """
    if not _table_exists("node_results"):
        return []
    conn = _get_db()
    rows = conn.execute(
        "SELECT DISTINCT SUBSTR(node_id, 1, INSTR(node_id, '/') - 1) AS rid FROM node_results WHERE node_id LIKE '%/%'"
    ).fetchall()
    return sorted(rid for rid in {r[0] for r in rows} if rid and rid not in NON_PROPERTY_NAMESPACES)
