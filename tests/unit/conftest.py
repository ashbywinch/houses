"""Pytest configuration — prevents external API calls and sheet writes."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

import dag.derived_node as _dn
from dag.derived_node import flush_processor as flush_processor
from houses.api_cache import set_cache_dir
from houses.config import settings
from tests.helpers import FakeCommuteRouter, FakeSchoolLookup, make_services


def flush_all() -> None:
    """Synchronously drain the stale queue — call this after seeding data
    to compute derived nodes before reading results."""
    import asyncio

    from dag.derived_node import flush_processor
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # No event loop in this thread (e.g. sync test in non-main thread).
        # Create one for the duration of the flush.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(flush_processor())
    loop.run_until_complete(flush_processor())

def _make_mock_services():
    return make_services(
        commute_router=FakeCommuteRouter(),
        school_lookup=FakeSchoolLookup(),
    )


@pytest.fixture(autouse=True)
def _clear_stale_queue():
    _dn._ensure_queue()
    while not _dn._stale_queue.empty():
        try:
            _dn._stale_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    yield


@pytest.fixture(autouse=True)
def _offline_scraper():
    saved = settings.rightmove_scraper_offline
    settings.rightmove_scraper_offline = True
    yield
    settings.rightmove_scraper_offline = saved


@pytest.fixture(autouse=True)
def _isolate_api_cache():
    with tempfile.TemporaryDirectory() as tmp:
        set_cache_dir(tmp)
        yield
        files = list(Path(tmp).iterdir())
        assert not files, (
            f"Unit test created {len(files)} cache file(s). "
            f"Cache files: {[f.name for f in files]}"
        )


@pytest.fixture(autouse=True)
def _no_sheet_writes():
    saved = settings.sheet_id
    settings.sheet_id = ""
    yield
    settings.sheet_id = saved


@pytest.fixture(autouse=True)
def _sqlite_memory():
    import sqlite3

    import dag.persistence as per
    saved = per._get_db
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    per._get_db = lambda: conn
    per.init_db()
    yield
    per._get_db = saved


@pytest.fixture(autouse=True)
def _mock_services():
    """Set mock services AFTER _sqlite_memory has switched to in-memory DB."""
    from houses.services_provider import _request_services as _sp
    token = _sp.set(_make_mock_services())
    yield
    _sp.reset(token)


@pytest.fixture(autouse=True)
def _isolate_settings_sources():
    from houses.services import _SETTINGS_SOURCE_CACHE
    _SETTINGS_SOURCE_CACHE.clear()
    yield
