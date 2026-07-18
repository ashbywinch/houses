"""
Critical test isolation fixtures — imported by conftest.py files.

These ensure tests never write to the production database or share
global state. Keep them here so that import edits in conftest.py
don't accidentally disable them.
"""

from __future__ import annotations

import sqlite3

import pytest

import dag.persistence as per
from dag.derived_node import _TestScheduler, set_scheduler


@pytest.fixture(autouse=True)
def _inject_test_scheduler():
    """Each test gets an isolated scheduler — no global queue leakage."""
    set_scheduler(_TestScheduler())
    yield


@pytest.fixture(autouse=True)
def _sqlite_memory():
    """Replace the global DB connection with an in-memory database.

    Every test uses an isolated in-memory SQLite so no test writes
    to the real ``data/houses.db``.
    """
    saved = per._get_db
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    per._get_db = lambda: conn
    per.init_db()
    yield
    per._get_db = saved
