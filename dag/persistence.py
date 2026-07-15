"""Generic persistence layer for DAG nodes.

Stores versioned source values and resolved derived values in SQLite.
Serialises complex types via TypeAdapter with ``_type``/``_module`` markers.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from houses.config import settings

DB_PATH: Path | None = None


class DagJSONEncoder(json.JSONEncoder):
    """Handles enums and other non-serializable types in DAG node results."""

    def default(self, o):
        if isinstance(o, Enum):
            return o.name.lower()
        return super().default(o)



def _get_db() -> sqlite3.Connection:
    global DB_PATH
    if DB_PATH is None:
        DB_PATH = Path(settings.sqlite_path)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _serialize_value(val: Any) -> str:
    if val is None:
        return ""
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
    except Exception:
        return str(val)


def _deserialize_value(raw: str) -> Any:
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
        except Exception:
            return d
    return d




def init_db(db_path: str | None = None) -> None:
    global DB_PATH
    if db_path:
        DB_PATH = Path(db_path)
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS node_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT NOT NULL,
            result_json TEXT NOT NULL,
            dep_timestamps TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_nr_node ON node_results(node_id, created_at DESC);
    """)



def save_node_result(node_id: str, result_dict: dict[str, Any],
                     dep_timestamps: dict[str, str] | None = None) -> int:
    """Persist a node's to_json() output to the node_results table.

    Each call appends a new row. The most recent row is the current value.
    """
    if not _table_exists("node_results"):
        init_db()
    conn = _get_db()
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO node_results (node_id, result_json, dep_timestamps, created_at)"
        " VALUES (?, ?, ?, ?)",
        (node_id,
         json.dumps(result_dict, cls=DagJSONEncoder),
         json.dumps(dep_timestamps, cls=DagJSONEncoder) if dep_timestamps else None,
         now),
    )
    conn.commit()
    return cur.lastrowid


def latest_node_result(node_id: str) -> dict[str, Any] | None:
    """Return the most recent to_json() dict for a node, or None."""
    if not _table_exists("node_results"):
        init_db()
        return None
    conn = _get_db()
    row = conn.execute(
        "SELECT result_json, dep_timestamps, created_at FROM node_results"
        " WHERE node_id=? ORDER BY created_at DESC LIMIT 1",
        (node_id,),
    ).fetchone()
    if row is None:
        return None
    result = json.loads(row["result_json"])
    result["_dep_timestamps"] = json.loads(row["dep_timestamps"]) if row["dep_timestamps"] else {}
    result["_persisted_at"] = row["created_at"]
    return result


def _table_exists(name: str) -> bool:
    conn = _get_db()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None
