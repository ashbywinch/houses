"""Comments persistence layer.

Stores per-property comments in a SQLite table.  Comments are user-generated
content — not DAG nodes — so they bypass the DAG entirely.

Uses ``houses.database.get_connection()`` for all database access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from houses.database import get_connection

logger = logging.getLogger(__name__)

_MIGRATION_TIMESTAMP = "1980-01-01T00:00:00+00:00"


@dataclass
class CommentEntry:
    person: str  # "Ashby" | "Simon" | "Lorena"
    text: str
    timestamp: str  # ISO 8601, set server-side


def get_comments(rid: str) -> list[dict[str, Any]]:
    """Return all comments for a property, oldest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT person, text, created_at FROM comments WHERE rid = ? ORDER BY created_at ASC",
        (rid,),
    ).fetchall()
    return [{"person": row["person"], "text": row["text"], "timestamp": row["created_at"]} for row in rows]


def add_comment(rid: str, person: str, text: str) -> dict[str, Any]:
    """Add a comment and return it as a dict."""
    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO comments (rid, person, text, created_at) VALUES (?, ?, ?, ?)",
        (rid, person, text, now),
    )
    conn.commit()
    return {"person": person, "text": text, "timestamp": now}


def migrate_old_comments(rid: str, old_comments: dict[str, Any]) -> list[dict[str, Any]]:
    """Migrate old flat comment fields to the new comments table on first request.

    Reads the ``comments`` dict from the detail API, inserts any non-empty
    values as comment rows, and returns the full comment list.

    The migration uses ``_MIGRATION_TIMESTAMP`` so that old comments sort
    before all real (user-created) comments.  The operation is idempotent
    — inserted inside a transaction with a double-check to prevent
    duplicate rows under concurrent access.
    """
    conn = get_connection()
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Check whether old comments have already been migrated for this RID
        already = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE rid = ? AND created_at = ?",
            (rid, _MIGRATION_TIMESTAMP),
        ).fetchone()[0]
        if already > 0:
            conn.commit()
            return get_comments(rid)

        field_map: dict[str, str] = {
            "group_notes": "Simon",
            "ashby_comments": "Ashby",
        }

        for field_name, person in field_map.items():
            val = old_comments.get(field_name, {})
            if isinstance(val, dict) and val.get("succeeded") and val.get("value"):
                text = str(val["value"]).strip()
                if text:
                    conn.execute(
                        "INSERT INTO comments (rid, person, text, created_at) VALUES (?, ?, ?, ?)",
                        (rid, person, text, _MIGRATION_TIMESTAMP),
                    )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_comments(rid)
