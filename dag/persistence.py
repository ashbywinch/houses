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


# lucidlint: ignore class-module small private helper — module keeps its domain name
class DagJSONEncoder(json.JSONEncoder):
    """Handles enums, Decimal, Money, Quantity, and other non-serializable types in DAG node results."""
    # lucidlint: ignore detached-method super().default(o) requires self — json.JSONEncoder dispatches via self.default
    @override
    def default(self, o):
        if isinstance(o, Enum):
            return o.name.lower()
        if isinstance(o, _Decimal):
            return float(o)
        if isinstance(o, _Money):
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            return {"amount": str(o.amount), "currency": o.currency}
        if isinstance(o, cast(type, Quantity)):
            m = float(o.magnitude)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            return {"value": int(m) if m == int(m) else m, "unit": str(o.units)}
        return super().default(o)


def _get_db() -> sqlite3.Connection:
    # lucidlint: ignore global-state bounded module cache/state — single writer, deliberate
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
    # lucidlint: ignore global-state bounded module cache/state — single writer, deliberate
    global DB_PATH
    if db_path:
        DB_PATH = Path(db_path)
    conn = _get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS node_results (
            node_id TEXT NOT NULL,
            result_json TEXT NOT NULL,
            dep_timestamps TEXT,
            created_at TEXT NOT NULL,
            code_version TEXT
        )
        """
    )
    # Latest-row lookups (latest_node_result, property_created_at) are the
    # hot path — the index was dropped in the code_version rewrite and a
    # fresh database must still get it.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nr_node ON node_results(node_id, created_at DESC);"
    )
    conn.commit()
    _ensure_code_version_column()


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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
    """
    if not _table_exists("node_results"):
        init_db()
    _ensure_code_version_column()
    conn = _get_db()
    now = created_at or datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO node_results (node_id, result_json, dep_timestamps, created_at, code_version)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            node_id,
            json.dumps(result_dict, cls=DagJSONEncoder),
            json.dumps(dep_timestamps, cls=DagJSONEncoder) if dep_timestamps else None,
            now,
            code_version,
        ),
    )
    conn.commit()
    rowid = cur.lastrowid
    return rowid if rowid is not None else 0


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def latest_node_result(node_id: str) -> dict[str, Any] | None:
    """Return the most recent to_json() dict for a node, or None."""
    if not _table_exists("node_results"):
        init_db()
        return None
    _ensure_code_version_column()
    conn = _get_db()
    row = conn.execute(
        "SELECT result_json, dep_timestamps, created_at, code_version FROM node_results"
        " WHERE node_id=? ORDER BY created_at DESC LIMIT 1",
        (node_id,),
    ).fetchone()
    if row is None:
        return None
    result = json.loads(row["result_json"])
    result["_dep_timestamps"] = json.loads(row["dep_timestamps"]) if row["dep_timestamps"] else {}
    result["_persisted_at"] = row["created_at"]
    result["_code_version"] = row["code_version"]
    return result


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
        "SELECT MIN(created_at) FROM node_results WHERE node_id LIKE ?",
        (f"{rid}/%",),
    ).fetchone()
    return row[0] if row and row[0] else None


def property_rids() -> list[str]:
    """Return distinct property RIDs from the node_results table."""
    if not _table_exists("node_results"):
        return []
    conn = _get_db()
    rows = conn.execute(
        "SELECT DISTINCT SUBSTR(node_id, 1, INSTR(node_id, '/') - 1) AS rid FROM node_results WHERE node_id LIKE '%/%'"
    ).fetchall()
    return sorted(set(r[0] for r in rows if r[0] and r[0].isdigit()))


