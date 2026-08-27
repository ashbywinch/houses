"""Application database connection manager.

Single owner of the SQLite connection for all application tables
(comments, etc.).  The DAG persistence layer in ``dag/persistence.py``
manages its own connection — this module covers application-level
storage only.

Usage::

    from houses.database import get_connection, init_db

    conn = get_connection()
    conn.execute("SELECT ...")

For tests, set ``database.testing = True`` and the connection will be
replaced with an in-memory SQLite database (same mechanism as
``dag.persistence.testing``).
"""

from __future__ import annotations

import contextvars
import logging
import sqlite3
from pathlib import Path

import dag.persistence as per
from houses.settings import settings

logger = logging.getLogger(__name__)

testing: bool = False
_connection_cache: contextvars.ContextVar[sqlite3.Connection | None] = contextvars.ContextVar(
    "_connection_cache", default=None
)


def get_connection() -> sqlite3.Connection:
    """Return a context-local SQLite connection with WAL mode enabled."""
    if testing:
        conn = per._get_db()
        conn.row_factory = sqlite3.Row
        return conn

    path = Path(settings.sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = _connection_cache.get()
    if conn is None:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _connection_cache.set(conn)
    return conn


def close_db() -> None:
    """Close the cached context-local connection and clear it."""
    conn = _connection_cache.get()
    if conn is not None:
        conn.close()
        _connection_cache.set(None)


_COMMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rid TEXT NOT NULL,
    person TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_COMMENTS_INDEX = "CREATE INDEX IF NOT EXISTS idx_comments_rid ON comments(rid, created_at ASC)"

# The scrape queue's table lives with the other app tables — its retry
# state must survive box restarts (see houses/scrape_queue.py for the
# queue logic; the DDL stays here to avoid an import cycle).
_SCRAPES_TABLE = """
CREATE TABLE IF NOT EXISTS pending_scrapes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rid TEXT NOT NULL,
    url TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    claimed_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
)
"""

_SCRAPES_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_scrapes_due ON pending_scrapes(next_retry_at, status)"
)


def init_db() -> None:
    """Create all application tables if they don't exist.

    Called once at server startup.  Idempotent — safe to call multiple
    times (e.g. in tests).
    """
    conn = get_connection()
    conn.execute(_COMMENTS_TABLE)
    conn.execute(_COMMENTS_INDEX)
    conn.execute(_SCRAPES_TABLE)
    conn.execute(_SCRAPES_INDEX)
    conn.commit()
