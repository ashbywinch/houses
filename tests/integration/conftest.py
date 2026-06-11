"""Integration test configuration — isolated temp cache, no sheet writes, offline scraper."""

import tempfile
from collections.abc import Callable
from unittest.mock import patch

import pytest
from httpx import AsyncClient, Client, MockTransport, Response

from houses.api_cache import set_cache_dir
from houses.config import settings


def mock_httpx():
    """Context manager that patches both ``httpx.AsyncClient`` and
    ``httpx.Client`` with a ``MockTransport`` that returns synthetic
    responses for every external API the enrichment pipeline calls.

    Yields the handler's call-list so tests can verify which
    APIs were hit (or not hit).
    """

    class _Handler:
        """Mock HTTP handler that tests can extend with custom rules."""

        def __init__(self):
            self.calls: list[str] = []
            self._rules: list[tuple[Callable[[str], bool], Callable] | None] = []

        def add_rule(self, matcher: Callable[[str], bool], responder: Callable) -> None:
            """Register a custom matcher/responder that takes priority."""
            self._rules.insert(0, (matcher, responder))

        def handler(self, request):
            url = str(request.url)
            self.calls.append(url)

            # Custom rules (added by specific tests) take priority
            for matcher, responder in self._rules:
                if matcher(url):
                    return responder(request)

            # Default rules
            if "tfl.gov.uk/Journey/JourneyResults" in url:
                return Response(200, json={"journeys": [{"duration": 30, "fare": {"totalCost": 500}}]})
            # postcodes.io
            if "api.postcodes.io" in url:
                return Response(200, json={"status": 200, "result": {"latitude": 51.5, "longitude": -0.1}})
            # ORS Directions (driving or walking)
            if "openrouteservice.org/v2/directions" in url:
                return Response(200, json={"routes": [{"summary": {"distance": 50, "duration": 1800}}]})
            # ORS Geocode
            if "openrouteservice.org/geocode" in url:
                return Response(200, json={"features": [{"geometry": {"coordinates": [-0.1, 51.5]}}]})
            # Google Maps Geocode
            if "maps.googleapis.com/maps/api/geocode" in url:
                return Response(200, json={"results": [{"geometry": {"location": {"lat": 51.5, "lng": -0.1}}}]})
            # Google Places
            if "places.googleapis.com" in url:
                return Response(200, json={"places": []})
            # Google Routes
            if "routes.googleapis.com" in url:
                return Response(200, json={"routes": [{"legs": [{"duration": "1800s"}]}]})
            # EPC
            if "get-energy-performance-data" in url:
                return Response(
                    200,
                    json={"data": [{"currentEnergyEfficiencyBand": "C", "registrationDate": "2023-01-01"}]},
                )
            # CivAccount
            if "civaccount.co.uk" in url:
                return Response(200, json={"band_d_rate": 1500.0})
            # Nominatim
            if "nominatim.openstreetmap.org" in url:
                return Response(200, json=[{"lat": "51.5", "lon": "-0.1"}])
            # OpenRouter LLM
            if "openrouter.ai" in url:
                return Response(200, json={"choices": [{"message": {"content": "A pleasant town."}}]})
            # Overpass
            if "overpass-api.de" in url:
                return Response(200, json={"elements": []})
            # VOA, council tax, school lookups
            if "tax.service.gov.uk" in url or "voa" in url.lower() or "get-information-schools" in url:
                return Response(200, json={})

            logger = __import__("logging").getLogger("test")
            logger.warning("Unhandled httpx request: %s %s", request.method, url)
            return Response(404)

    counter = _Handler()

    def _patch_client(original_init, handler):
        def patched_init(self, **kwargs):
            kwargs["transport"] = MockTransport(handler)
            original_init(self, **kwargs)

        return patched_init

    original_async_init = AsyncClient.__init__
    original_sync_init = Client.__init__

    async_patch = patch.object(AsyncClient, "__init__", _patch_client(original_async_init, counter.handler))
    sync_patch = patch.object(Client, "__init__", _patch_client(original_sync_init, counter.handler))

    return counter, async_patch, sync_patch


@pytest.fixture(autouse=True)
def _offline_scraper():
    """Prevent the scraper from starting Chrome during tests."""
    saved = settings.rightmove_scraper_offline
    settings.rightmove_scraper_offline = True
    yield
    settings.rightmove_scraper_offline = saved


@pytest.fixture(autouse=True)
def _isolate_api_cache():
    """Isolate the disk API cache to a temp directory per test.

    Integration tests that need pre-seeded cache from ``tests/fixtures/api_cache/``
    should copy fixture files to the tempdir after calling this fixture.
    """
    with tempfile.TemporaryDirectory() as tmp:
        set_cache_dir(tmp)
        yield


@pytest.fixture(autouse=True)
def _mock_http_requests():
    """Every integration test must mock external HTTP APIs.

    This autouse fixture applies ``mock_httpx()`` to every test, so tests
    never hit real APIs.  Tests that need different mock responses can
    inspect or replace ``handler.calls``.
    """
    counter, async_patch, sync_patch = mock_httpx()
    with async_patch, sync_patch:
        yield counter


@pytest.fixture(autouse=True)
def _no_sheet_writes():
    """Prevent integration tests from touching a real Google Sheet."""
    saved = settings.sheet_id
    settings.sheet_id = ""
    yield
    settings.sheet_id = saved
