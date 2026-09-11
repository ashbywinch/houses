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


@dataclass(frozen=True)
class CommentEntry:
    """A comment as stored and served — one row of the comments wire shape."""

    person: str  # "Ashby" | "Simon" | "Lorena"
    text: str
    timestamp: str  # ISO 8601, set server-side

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the comment wire shape (coding-standards.md)
        return {"person": self.person, "text": self.text, "timestamp": self.timestamp}


# lucidlint: ignore record-shape wire-format dict — serialization boundary
def get_comments(rid: str) -> list[dict[str, Any]]:
    """Return all comments for a property, oldest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT person, text, created_at FROM comments WHERE rid = ? ORDER BY created_at ASC",
        (rid,),
    ).fetchall()
    return [CommentEntry(person=row["person"], text=row["text"], timestamp=row["created_at"]).to_dict() for row in rows]


# lucidlint: ignore record-shape wire-format dict — serialization boundary
def add_comment(rid: str, person: str, text: str) -> dict[str, Any]:
    """Add a comment and return it as a dict."""
    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO comments (rid, person, text, created_at) VALUES (?, ?, ?, ?)",
        (rid, person, text, now),
    )
    conn.commit()
    return CommentEntry(person=person, text=text, timestamp=now).to_dict()
