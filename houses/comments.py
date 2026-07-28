"""Comments persistence layer.

Stores per-property comments in a SQLite table.  Comments are user-generated
content — not DAG nodes — so they bypass the DAG entirely.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from houses.config import settings

logger = logging.getLogger(__name__)

_db_path: Path | None = None
testing: bool = False
_connection_cache = threading.local()


@dataclass
class CommentEntry:
    person: str  # "Ashby" | "Simon" | "Lorena"
    text: str
    timestamp: str  # ISO 8601, set server-side


def _get_db() -> sqlite3.Connection:
    global _db_path
    if _db_path is None:
        _db_path = Path(settings.sqlite_path)
    if not hasattr(_connection_cache, "conn") or _connection_cache.conn is None:
        _connection_cache.conn = sqlite3.connect(str(_db_path))
        _connection_cache.conn.row_factory = sqlite3.Row
        if testing:
            _connection_cache.conn.execute("PRAGMA foreign_keys = ON")
    return _connection_cache.conn


def close_db() -> None:
    if hasattr(_connection_cache, "conn") and _connection_cache.conn is not None:
        _connection_cache.conn.close()
        _connection_cache.conn = None


def init_comments_db() -> None:
    """Create the comments table if it doesn't exist."""
    conn = _get_db()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS comments ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  rid TEXT NOT NULL,"
        "  person TEXT NOT NULL,"
        "  text TEXT NOT NULL,"
        "  created_at TEXT NOT NULL"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_rid ON comments(rid, created_at ASC)")
    conn.commit()


def get_comments(rid: str) -> list[dict[str, Any]]:
    """Return all comments for a property, oldest first."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT person, text, created_at FROM comments WHERE rid = ? ORDER BY created_at ASC",
        (rid,),
    ).fetchall()
    return [{"person": row["person"], "text": row["text"], "timestamp": row["created_at"]} for row in rows]


def add_comment(rid: str, person: str, text: str) -> dict[str, Any]:
    """Add a comment and return it as a dict."""
    conn = _get_db()
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO comments (rid, person, text, created_at) VALUES (?, ?, ?, ?)",
        (rid, person, text.strip(), now),
    )
    conn.commit()
    return {"person": person, "text": text.strip(), "timestamp": now}


def migrate_old_comments(rid: str, old_comments: dict[str, Any]) -> list[dict[str, Any]]:
    """Migrate old flat comment fields to the new comments table on first request.

    Reads the ``comments`` dict from the detail API, inserts any non-empty
    values as comment rows, and returns the full comment list.
    """
    conn = _get_db()
    existing = conn.execute("SELECT COUNT(*) FROM comments WHERE rid = ?", (rid,)).fetchone()[0]
    if existing > 0:
        return get_comments(rid)

    field_map: dict[str, str] = {
        "group_notes": "Group",
        "ashby_comments": "Ashby",
    }

    now = datetime.now(UTC).isoformat()
    for field_name, person in field_map.items():
        val = old_comments.get(field_name, {})
        if isinstance(val, dict) and val.get("succeeded") and val.get("value"):
            text = str(val["value"]).strip()
            if text:
                conn.execute(
                    "INSERT INTO comments (rid, person, text, created_at) VALUES (?, ?, ?, ?)",
                    (rid, person, text, now),
                )

    conn.commit()
    return get_comments(rid)
