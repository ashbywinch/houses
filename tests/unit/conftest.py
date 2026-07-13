"""Pytest configuration — prevents external API calls and sheet writes."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

import dag.derived_node as _dn
from dag.attempt import Attempt
from dag.derived_node import flush_processor as flush_processor
from houses.api_cache import set_cache_dir
from houses.config import settings
from houses.geo import GeoPoint
from houses.services_provider import _request_services
from tests.helpers import FakeCommuteRouter, FakeSchoolLookup


def _make_mock_services():
    from houses.services import Services
    svc = Services(
        commute_router=FakeCommuteRouter(),
        school_lookup=FakeSchoolLookup(),
    )

    async def _fake_geocode(self, a):
        return Attempt.succeeded(GeoPoint(51.5, -0.1))

    async def _fake_geocode_postcode(self, p):
        return Attempt.succeeded(GeoPoint(51.5, -0.1))

    svc.geocoder = type("_Fake", (), {
        "geocode_address": _fake_geocode,
        "geocode_postcode": _fake_geocode_postcode,
    })()
    return svc


_request_services.set(_make_mock_services())

_orig_attempt = _dn.DerivedNode.attempt
_flushing = False


async def _flushing_attempt(self):
    global _flushing
    if not _flushing:
        _flushing = True
        try:
            await flush_processor()
        finally:
            _flushing = False
    return await _orig_attempt(self)


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
def _patch_attempt():
    _dn.DerivedNode.attempt = _flushing_attempt
    yield
    _dn.DerivedNode.attempt = _orig_attempt


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
def _no_geocoding(monkeypatch):
    async def fake_geocode(_address: str) -> None:
        return None
    monkeypatch.setattr("houses.model.property._geocode_address", fake_geocode)


@pytest.fixture(autouse=True)
def _isolate_settings_sources():
    from houses.services import _SETTINGS_SOURCE_CACHE
    _SETTINGS_SOURCE_CACHE.clear()
    yield
