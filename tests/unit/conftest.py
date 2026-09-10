"""Pytest configuration — prevents external API calls."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from dag.scheduler import flush_processor
from houses.api_cache import set_cache_dir
from houses.nodes.bus import BusRouteNode
from tests.helpers import FakeSchoolLookup, make_services
from tests.unit.isolation_fixtures import (  # noqa: F401, F811
    _inject_test_scheduler,
    _reset_global_state,
    _sqlite_memory,
)

# Prevent BusRouteNode from making real Google Routes API calls in unit tests.
# bus.py sets _default_google_routes_post = _router._google_routes_post at
# import time; this override must run after that import but before any test.
BusRouteNode._default_google_routes_post = None


def drain_recompute() -> int:
    """Drain the DAG work queue (event-loop dance included). Each item
    runs compute AND its persistence step — exactly what the processor
    would do — synchronously on the test thread. Returns the number of
    items processed."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    # ONE drain: a node's refresh queues its dependents inside the same
    # drain loop, so a second call could only mask a queue bug.
    return loop.run_until_complete(flush_processor())


def flush_all() -> None:
    """Drain the DAG work queue ONCE, after the operation under test,
    before assertions — compute and persistence land together in
    scheduled order. In production reads never wait on the queue at all
    (docs/dag-library.md → 'Thread rules')."""
    import dag.scheduler as _dag_sched

    sched = _dag_sched.get_scheduler()
    assert isinstance(sched, _dag_sched.AsyncQueueScheduler)
    enqueued_before = sched.enqueued_since_flush
    drained = drain_recompute()
    if drained == 0 and enqueued_before == 0:
        raise RuntimeError(
            "flush_all() drained nothing and nothing was enqueued since the "
            "last flush. flush_all() is a once-per-operation drain — work "
            "runs on the processor by design and reads never wait on the "
            "queue. See docs/dag-library.md → 'Thread rules'."
        )


def _make_mock_services():
    return make_services(
        school_lookup=FakeSchoolLookup(),
    )


@pytest.fixture(autouse=True)
def _mock_google_routes(_sqlite_memory, _reset_global_state, _isolate_settings_sources):  # noqa: F811
    """Prevent WalkNode, DriveNode, BusRouteNode, and TflTransitNode from making real API calls.

    WalkNode/DriveNode call through ``get_services().route_planner`` — the
    default fakes in tests/helpers return canned routes. TflTransitNode
    builds its client via ``get_services().tfl_client_factory`` — the
    default fake returns impossible. BusRouteNode receives
    ``google_routes_post`` from the services ``commute_router`` (no post).

    Depends on the isolation fixtures explicitly: make_services() pushes
    default settings through the guarded SettingsNode.push, which needs
    test mode armed (and the settings cache empty) or it refuses.
    """
    from houses.services_provider import _request_services as _sp

    token = _sp.set(make_services())
    yield
    _sp.reset(token)


@pytest.fixture(autouse=True)
def _isolate_api_cache():
    with tempfile.TemporaryDirectory() as tmp:
        set_cache_dir(tmp)
        yield
        files = list(Path(tmp).iterdir())
        assert not files, f"Unit test created {len(files)} cache file(s): {[f.name for f in files]}"


@pytest.fixture(autouse=True)
def _isolate_settings_sources():
    """Clear the settings-source cache so _make_settings_source always
    reads from the per-test in-memory DB or factory, never stale state."""
    from houses.services import SETTINGS_SOURCE_CACHE

    SETTINGS_SOURCE_CACHE.clear()
    yield
    SETTINGS_SOURCE_CACHE.clear()


@pytest.fixture(autouse=True)
def _mock_services(_sqlite_memory, _reset_global_state, _isolate_settings_sources):  # noqa: F811
    """Set mock services AFTER in-memory DB and empty settings cache."""
    from houses.services_provider import _request_services as _sp

    token = _sp.set(_make_mock_services())
    yield
    _sp.reset(token)
