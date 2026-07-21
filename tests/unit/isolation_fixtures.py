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
from dag.derived_node import AsyncQueueScheduler, set_scheduler


@pytest.fixture(autouse=True)
def _inject_test_scheduler():
    """Each test gets an isolated scheduler — no global queue leakage."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))
    yield


@pytest.fixture(autouse=True)
def _sqlite_memory():
    """Replace the global DB connection with an in-memory database.

    Every test uses an isolated in-memory SQLite so no test writes
    to the real ``data/houses.db``.
    """
    saved = per._get_db
    per.testing = True
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    per._get_db = lambda: conn
    per.init_db()
    yield
    per.testing = False
    per._get_db = saved

from houses.services import _reset_settings_cache
from houses.property_registry import _reset as _reset_property_registry
from houses.web.broadcaster import _reset as _reset_broadcaster
from houses.town_desc import _reset as _reset_town_desc
from houses.council_tax import _reset as _reset_council_tax


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset all process-wide mutable globals between tests."""
    _reset_settings_cache()
    _reset_property_registry()
    _reset_broadcaster()
    _reset_town_desc()
    _reset_council_tax()
    yield
