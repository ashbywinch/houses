"""Pytest configuration — prevents external API calls and sheet writes."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from dag.attempt import Attempt
from dag.scheduler import flush_processor
from houses.api_cache import set_cache_dir
from houses.config import settings
from houses.nodes.bus import BusRouteNode
from tests.helpers import FakeCommuteRouter, FakeSchoolLookup, make_services
from tests.unit.isolation_fixtures import (  # noqa: F401, F811
    _inject_test_scheduler,
    _reset_global_state,
    _sqlite_memory,
)

# Prevent BusRouteNode from making real Google Routes API calls in unit tests.
# bus.py sets _default_google_routes_post = _router._google_routes_post at
# import time; this override must run after that import but before any test.
BusRouteNode._default_google_routes_post = None


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
def _mock_google_routes(monkeypatch):
    """Prevent WalkNode, DriveNode, BusRouteNode, and TflTransitNode from making real API calls.

    WalkNode/DriveNode call through ``CommuteRouter._google_route_commute``.
    BusRouteNode receives ``_google_routes_post`` via the property.
    TflTransitNode calls ``TflClient.plan()`` directly.
    """

    async def mock_google_routes(*_, **__):
        return Attempt.impossible("mocked — unit test")

    async def mock_tfl_plan(self):
        return Attempt.impossible("mocked — unit test")

    monkeypatch.setattr("houses.routing.CommuteRouter._google_route_commute", mock_google_routes)
    monkeypatch.setattr("houses.routing.CommuteRouter.google_routes_post", None)
    monkeypatch.setattr("houses.tfl_client.TflClient.plan", mock_tfl_plan)


@pytest.fixture(autouse=True)
def _isolate_api_cache():
    with tempfile.TemporaryDirectory() as tmp:
        set_cache_dir(tmp)
        yield
        files = list(Path(tmp).iterdir())
        assert not files, f"Unit test created {len(files)} cache file(s): {[f.name for f in files]}"


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
def _mock_services(_sqlite_memory, _reset_global_state, _isolate_settings_sources):  # noqa: F811
    """Set mock services AFTER in-memory DB and empty settings cache."""
    from houses.services_provider import _request_services as _sp

    token = _sp.set(_make_mock_services())
    yield
    _sp.reset(token)
