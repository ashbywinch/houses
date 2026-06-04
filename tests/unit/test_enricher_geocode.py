"""Tests for enricher geocoding — uses httpx MockTransport for the HTTP layer."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient, MockTransport, Response

from houses.enricher import _geo_cache, _geocode


@pytest.fixture(autouse=True)
def _clear_geo_cache():
    """Clear the module-level geo cache between tests for isolation."""
    _geo_cache.clear()


@pytest.mark.asyncio
async def test_geocode_full_postcode():
    """A valid full postcode should return lat/lng from postcodes.io."""

    def handler(request):
        assert "api.postcodes.io/postcodes/RG14" in str(request.url)
        return Response(
            200,
            json={
                "status": 200,
                "result": {"latitude": 51.4, "longitude": -1.32},
            },
        )

    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs.setdefault("transport", MockTransport(handler))
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        result = await _geocode("RG14 1AA")

    assert result == (51.4, -1.32)


@pytest.mark.asyncio
async def test_geocode_outcode():
    """An outcode (e.g. SL6) should hit the outcode endpoint."""

    def handler(request):
        assert "api.postcodes.io/outcodes/SL6" in str(request.url)
        return Response(
            200,
            json={
                "status": 200,
                "result": {"latitude": 51.5, "longitude": -0.7},
            },
        )

    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs.setdefault("transport", MockTransport(handler))
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        result = await _geocode("SL6")

    assert result == (51.5, -0.7)


@pytest.mark.asyncio
async def test_geocode_caches_result():
    """Geocoding the same postcode twice should only hit the API once."""

    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return Response(
            200,
            json={
                "status": 200,
                "result": {"latitude": 51.4, "longitude": -1.32},
            },
        )

    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs.setdefault("transport", MockTransport(handler))
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        result1 = await _geocode("RG14 1AA")
        result2 = await _geocode("RG14 1AA")

    assert result1 == (51.4, -1.32)
    assert result2 == (51.4, -1.32)
    assert call_count == 1, f"Expected 1 API call, got {call_count}"


@pytest.mark.asyncio
async def test_geocode_404_caches_none():
    """A 404 response should cache None and not retry."""

    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return Response(404)

    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs.setdefault("transport", MockTransport(handler))
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        result1 = await _geocode("RG14 1AA")
        result2 = await _geocode("RG14 1AA")

    assert result1 is None
    assert result2 is None
    # Only the first call hits the API; 404 is cached for subsequent calls
    assert call_count == 1


@pytest.mark.asyncio
async def test_geocode_empty_postcode():
    """Empty postcode should return None without making HTTP calls."""

    def handler(request):
        raise AssertionError("Should not be called")

    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs.setdefault("transport", MockTransport(handler))
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        result = await _geocode("")

    assert result is None


@pytest.mark.asyncio
async def test_geocode_normalises_case():
    """Postcode should be uppercased before lookup."""

    def handler(request):
        assert "RG14" in str(request.url)
        return Response(
            200,
            json={
                "status": 200,
                "result": {"latitude": 51.4, "longitude": -1.32},
            },
        )

    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs.setdefault("transport", MockTransport(handler))
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        result = await _geocode("rg14 1aa")

    assert result == (51.4, -1.32)
