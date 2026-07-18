"""Pytest configuration — prevents external API calls and sheet writes."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from dag.derived_node import flush_processor
from houses.api_cache import set_cache_dir
from houses.config import settings
from tests.helpers import FakeCommuteRouter, FakeSchoolLookup, make_services
from tests.unit.isolation_fixtures import _inject_test_scheduler, _sqlite_memory  # noqa: F401, F811


def flush_all() -> None:
    """Synchronously drain the stale queue — call this after seeding data
    to compute derived nodes before reading results."""

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
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
        assert not files, f"Unit test created {len(files)} cache file(s). Cache files: {[f.name for f in files]}"


@pytest.fixture(autouse=True)
def _no_sheet_writes():
    saved = settings.sheet_id
    settings.sheet_id = ""
    yield
    settings.sheet_id = saved


@pytest.fixture(autouse=True)
def _isolate_settings_sources():
    """Clear the settings-source cache so _make_settings_source always
    reads from the per-test in-memory DB or factory, never stale state."""
    from houses.services import _SETTINGS_SOURCE_CACHE

    _SETTINGS_SOURCE_CACHE.clear()
    yield
    _SETTINGS_SOURCE_CACHE.clear()


@pytest.fixture(autouse=True)
def _mock_services(_sqlite_memory, _isolate_settings_sources):  # noqa: F811
    """Set mock services AFTER in-memory DB and empty settings cache."""
    from houses.services_provider import _request_services as _sp
    token = _sp.set(_make_mock_services())
    yield
    _sp.reset(token)
