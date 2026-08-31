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


@dataclass
class CommentEntry:
    person: str  # "Ashby" | "Simon" | "Lorena"
    text: str
    timestamp: str  # ISO 8601, set server-side


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def get_comments(rid: str) -> list[dict[str, Any]]:
    """Return all comments for a property, oldest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT person, text, created_at FROM comments WHERE rid = ? ORDER BY created_at ASC",
        (rid,),
    ).fetchall()
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return [{"person": row["person"], "text": row["text"], "timestamp": row["created_at"]} for row in rows]


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def add_comment(rid: str, person: str, text: str) -> dict[str, Any]:
    """Add a comment and return it as a dict."""
    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO comments (rid, person, text, created_at) VALUES (?, ?, ?, ?)",
        (rid, person, text, now),
    )
    conn.commit()
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {"person": person, "text": text, "timestamp": now}


