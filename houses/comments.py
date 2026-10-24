"""Comments persistence layer.

Stores per-property comments in a SQLite table.  Comments are user-generated
content — not DAG nodes — so they bypass the DAG entirely.

Uses ``houses.database.get_connection()`` for all database access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict

from houses.database import get_connection

logger = logging.getLogger(__name__)


class Comment(TypedDict):
    """One comment row of the comments wire shape."""

    person: str
    text: str
    timestamp: str


@dataclass(frozen=True)
class CommentEntry:
    """A comment as stored and served — one row of the comments wire shape."""

    person: str  # "Ashby" | "Simon" | "Lorena"
    text: str
    timestamp: str  # ISO 8601, set server-side

    def to_dict(self) -> Comment:
        return Comment(person=self.person, text=self.text, timestamp=self.timestamp)


def get_comments(rid: str) -> list[Comment]:
    """Return all comments for a property, oldest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT person, text, created_at FROM comments WHERE rid = ? ORDER BY created_at ASC",
        (rid,),
    ).fetchall()
    return [CommentEntry(person=row["person"], text=row["text"], timestamp=row["created_at"]).to_dict() for row in rows]


def add_comment(rid: str, person: str, text: str) -> Comment:
    """Add a comment and return it as its wire record."""
    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO comments (rid, person, text, created_at) VALUES (?, ?, ?, ?)",
        (rid, person, text, now),
    )
    conn.commit()
    return CommentEntry(person=person, text=text, timestamp=now).to_dict()
