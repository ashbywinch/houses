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

import logging
import sqlite3
import threading
from pathlib import Path

from houses.config import settings

logger = logging.getLogger(__name__)

testing: bool = False
_connection_cache = threading.local()


def get_connection() -> sqlite3.Connection:
    """Return a thread-local SQLite connection with WAL mode enabled."""
    if testing:
        import dag.persistence as per

        conn = per._get_db()
        conn.row_factory = sqlite3.Row
        return conn

    path = Path(settings.sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not hasattr(_connection_cache, "conn") or _connection_cache.conn is None:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _connection_cache.conn = conn
    return _connection_cache.conn


def close_db() -> None:
    """Close the cached thread-local connection and clear it."""
    if hasattr(_connection_cache, "conn") and _connection_cache.conn is not None:
        _connection_cache.conn.close()
        _connection_cache.conn = None


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


def init_db() -> None:
    """Create all application tables if they don't exist.

    Called once at server startup.  Idempotent — safe to call multiple
    times (e.g. in tests).
    """
    conn = get_connection()
    conn.execute(_COMMENTS_TABLE)
    conn.execute(_COMMENTS_INDEX)
    conn.commit()
