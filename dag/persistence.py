"""Generic persistence layer for DAG nodes.

Stores versioned source values and resolved derived values in SQLite.
Serialises complex types via TypeAdapter with ``_type``/``_module`` markers.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

DB_PATH: Path | None = None


def _get_db() -> sqlite3.Connection:
    global DB_PATH
    if DB_PATH is None:
        DB_PATH = Path("data/dag.db")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _serialize_value(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (str, int, float, bool)):
        return str(val)
    try:
        ta = TypeAdapter(type(val))
        d = ta.dump_python(val)
        if isinstance(d, dict):
            d["_type"] = type(val).__name__
            d["_module"] = type(val).__module__
        return json.dumps(d)
    except Exception:
        return str(val)


def _deserialize_value(raw: str) -> Any:
    if not raw or raw == "None":
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
        except Exception:
            return d
    return d


def init_db(db_path: str | None = None) -> None:
    global DB_PATH
    if db_path:
        DB_PATH = Path(db_path)
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS source_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sv_prop_node ON source_values(property_id, node_id);

        CREATE TABLE IF NOT EXISTS derived_values (
            property_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT NOT NULL,
            error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (property_id, node_id)
        );
    """)
    conn.commit()


def insert_source_value(property_id: str, node_id: str,
                        value: Any, source: str) -> int:
    conn = _get_db()
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO source_values (property_id, node_id, value, source, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (property_id, node_id, _serialize_value(value), source, now),
    )
    conn.commit()
    return cur.lastrowid


def get_latest_source_value(property_id: str,
                            node_id: str) -> dict[str, Any] | None:
    conn = _get_db()
    row = conn.execute(
        "SELECT value, source, created_at FROM source_values"
        " WHERE property_id=? AND node_id=?"
        " ORDER BY created_at DESC LIMIT 1",
        (property_id, node_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "value": _deserialize_value(row["value"]),
        "source": row["source"],
        "created_at": row["created_at"],
    }


def get_all_source_values(property_id: str) -> dict[str, dict[str, Any]]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT node_id, value, source, created_at FROM source_values"
        " WHERE property_id=? ORDER BY created_at DESC",
        (property_id,),
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for row in rows:
        nid = row["node_id"]
        if nid not in seen:
            seen.add(nid)
            result[nid] = {
                "value": _deserialize_value(row["value"]),
                "source": row["source"],
                "created_at": row["created_at"],
            }
    return result


def load_node_data(property_id: str) -> dict[str, Any]:
    """Load all stored data for a property."""
    return {
        "sources": get_all_source_values(property_id),
    }
