from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from houses.config import settings
from houses.geo import GeoPoint
from houses.model import DerivedRow, PropertyData, SourceRow, UserRow


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

    ta = TypeAdapter(type(val))
    d = ta.dump_python(val)
    if isinstance(d, dict):
        d["_type"] = type(val).__qualname__
        d["_module"] = type(val).__module__
    return json.dumps(d)


def _deserialize_value(raw: str) -> Any:
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(d, dict) and "_type" in d and "_module" in d:
        import importlib

        mod = importlib.import_module(d["_module"])
        cls = getattr(mod, d["_type"])
        try:
            return TypeAdapter(cls).validate_python(d)
        except Exception:
            return raw
    if isinstance(d, dict) and "lat" in d and "lon" in d:
        try:
            return GeoPoint(lat=d["lat"], lon=d["lon"])
        except Exception:
            return raw
    return d




DB_PATH: Path | None = None


def get_db() -> sqlite3.Connection:
    global DB_PATH
    if DB_PATH is None:
        DB_PATH = Path(settings.sqlite_path)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    global DB_PATH
    if db_path:
        DB_PATH = Path(db_path)
    conn = get_db()
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

        CREATE TABLE IF NOT EXISTS user_corrected_address (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            obsoleted_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_uca_prop ON user_corrected_address(property_id);

        CREATE TABLE IF NOT EXISTS user_precise_location (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            obsoleted_at TEXT
        );

        CREATE TABLE IF NOT EXISTS derived_values (
            property_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            value TEXT NOT NULL,
            dep_versions TEXT NOT NULL,
            source TEXT NOT NULL,
            error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (property_id, node_id)
        );
    """)
    conn.commit()


def insert_source_value(property_id: str, node_id: str, value: Any, source: str) -> int:
    conn = get_db()
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO source_values (property_id, node_id, value, source, created_at) VALUES (?, ?, ?, ?, ?)",
        (property_id, node_id, _serialize_value(value), source, now),
    )
    conn.commit()
    return cur.lastrowid


def get_latest_source_value(property_id: str, node_id: str) -> SourceRow | None:
    conn = get_db()
    row = conn.execute(
        "SELECT id, value, source, created_at FROM source_values"
        " WHERE property_id=? AND node_id=? ORDER BY created_at DESC LIMIT 1",
        (property_id, node_id),
    ).fetchone()
    if row is None:
        return None
    return SourceRow(
        row_id=row["id"],
        value=_deserialize_value(row["value"]),
        source=row["source"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def get_all_source_values(property_id: str) -> dict[str, SourceRow]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, node_id, value, source, created_at FROM source_values WHERE property_id=? ORDER BY created_at DESC",
        (property_id,),
    ).fetchall()
    result: dict[str, SourceRow] = {}
    seen: set[str] = set()
    for row in rows:
        nid = row["node_id"]
        if nid not in seen:
            seen.add(nid)
            result[nid] = SourceRow(
                row_id=row["id"],
                value=_deserialize_value(row["value"]),
                source=row["source"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
    return result


USER_TABLE_NODES = {
    "corrected_address": "user_corrected_address",
    "precise_location": "user_precise_location",
}


def insert_user_input(property_id: str, node_id: str, value: Any) -> int:
    conn = get_db()
    table = USER_TABLE_NODES[node_id]
    now = datetime.now(UTC).isoformat()
    conn.execute(
        f"UPDATE {table} SET obsoleted_at=? WHERE property_id=? AND obsoleted_at IS NULL",
        (now, property_id),
    )
    cur = conn.execute(
        f"INSERT INTO {table} (property_id, value, created_at) VALUES (?, ?, ?)",
        (property_id, _serialize_value(value), now),
    )
    conn.commit()
    return cur.lastrowid


def get_current_user_input(property_id: str, node_id: str) -> UserRow | None:
    conn = get_db()
    table = USER_TABLE_NODES[node_id]
    row = conn.execute(
        f"SELECT id, value, created_at FROM {table}"
        " WHERE property_id=? AND obsoleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (property_id,),
    ).fetchone()
    if row is None:
        return None
    return UserRow(
        row_id=row["id"],
        value=_deserialize_value(row["value"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def get_all_user_inputs(property_id: str) -> dict[str, UserRow]:
    result: dict[str, UserRow] = {}
    for node_id in USER_TABLE_NODES:
        row = get_current_user_input(property_id, node_id)
        if row is not None:
            result[node_id] = row
    return result


def get_source_row_timestamp(property_id: str, node_id: str) -> datetime | None:
    conn = get_db()
    row = conn.execute(
        "SELECT created_at FROM source_values WHERE property_id=? AND node_id=? ORDER BY created_at DESC LIMIT 1",
        (property_id, node_id),
    ).fetchone()
    return datetime.fromisoformat(row["created_at"]) if row else None


def get_user_row_timestamp(property_id: str, node_id: str) -> datetime | None:
    conn = get_db()
    table = USER_TABLE_NODES.get(node_id)
    if table is None:
        return None
    row = conn.execute(
        f"SELECT created_at FROM {table} WHERE property_id=? AND obsoleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (property_id,),
    ).fetchone()
    return datetime.fromisoformat(row["created_at"]) if row else None


def get_dep_timestamp(property_id: str, node_id: str) -> datetime | None:
    if node_id in USER_TABLE_NODES:
        return get_user_row_timestamp(property_id, node_id)
    return get_source_row_timestamp(property_id, node_id)


def load_property_data(rid: str) -> PropertyData:
    sources = get_all_source_values(rid)
    user_inputs = get_all_user_inputs(rid)
    derived = get_all_derived_values(rid)
    return PropertyData(rid=rid, sources=sources, user_inputs=user_inputs, derived=derived)


def get_all_derived_values(property_id: str) -> dict[str, DerivedRow]:
    conn = get_db()
    rows = conn.execute(
        "SELECT node_id, value, dep_versions, source, error, updated_at FROM derived_values WHERE property_id=?",
        (property_id,),
    ).fetchall()
    result: dict[str, DerivedRow] = {}
    for row in rows:
        result[row["node_id"]] = DerivedRow(
            value=_deserialize_value(row["value"]),
            dep_versions=json.loads(row["dep_versions"]),
            source=row["source"],
            error=row["error"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
    return result


def save_derived(property_id: str, node_id: str, dr: DerivedRow) -> None:
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO derived_values
           (property_id, node_id, value, dep_versions, source, error, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            property_id,
            node_id,
            _serialize_value(dr.value),
            json.dumps(dr.dep_versions),
            dr.source,
            dr.error,
            dr.updated_at.isoformat(),
        ),
    )
    conn.commit()
