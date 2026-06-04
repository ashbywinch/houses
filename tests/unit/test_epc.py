"""Tests for EPC lookup — uses httpx MockTransport for the HTTP layer."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient, MockTransport, Response

from houses.config import settings
from houses.epc import lookup_epc


@pytest.fixture(autouse=True)
def _ensure_token():
    """Ensure EPC token is set for tests that exercise the HTTP path."""
    saved = settings.epc_bearer_token
    settings.epc_bearer_token = "test-token"
    yield
    settings.epc_bearer_token = saved


@pytest.mark.asyncio
async def test_returns_band_from_most_recent_certificate():
    """Should return the band from the most recent certificate by registration date."""

    def handler(request):
        assert "postcode=RG14" in str(request.url)
        return Response(
            200,
            json={
                "data": [
                    {"registrationDate": "2020-01-01", "currentEnergyEfficiencyBand": "D"},
                    {"registrationDate": "2024-06-15", "currentEnergyEfficiencyBand": "C"},
                    {"registrationDate": "2022-03-10", "currentEnergyEfficiencyBand": "B"},
                ]
            },
        )

    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs.setdefault("transport", MockTransport(handler))
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        band = await lookup_epc("RG14 1AA")

    assert band == "C"  # Most recent (2024)


@pytest.mark.asyncio
async def test_no_certificates_returns_empty():
    """No certificates should return empty string."""

    def handler(request):
        return Response(200, json={"data": []})

    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs.setdefault("transport", MockTransport(handler))
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        band = await lookup_epc("SL6")

    assert band == ""


@pytest.mark.asyncio
async def test_non_200_response_returns_empty():
    """API returning non-200 should be handled gracefully."""

    def handler(request):
        return Response(500)

    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs.setdefault("transport", MockTransport(handler))
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        band = await lookup_epc("RG14 1AA")

    assert band == ""


@pytest.mark.asyncio
async def test_no_token_returns_empty():
    """With no bearer token, returns empty without making any HTTP call."""

    def handler(request):
        raise AssertionError("Should not be called")

    settings.epc_bearer_token = ""
    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs.setdefault("transport", MockTransport(handler))
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        band = await lookup_epc("RG14 1AA")

    assert band == ""
