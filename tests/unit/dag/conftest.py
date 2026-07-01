from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _sqlite_memory():
    """Replace the global DB connection with an in-memory database.

    Every test in this directory uses an isolated in-memory SQLite
    so no test writes to the real ``data/dag.db``.
    """
    import dag.persistence as per

    saved = per._get_db
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    per._get_db = lambda: conn
    per.init_db()
    yield
    per._get_db = saved
