"""Integration tests that hit real external APIs.

Run with:  make test-integration
Skip with:  make test  (unit tests only)
"""

import os

import httpx
import pytest

from houses.enricher import OUTCODES_IO_URL, POSTCODES_IO_URL
from houses.server import extract_postcode

pytestmark = pytest.mark.integration


class TestPostcodesIO:
    @pytest.mark.asyncio
    async def test_full_postcode_geocode(self):
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{POSTCODES_IO_URL}/SW1A%201AA")
        assert resp.status_code == 200
        data = resp.json()
        lat = data["result"]["latitude"]
        lng = data["result"]["longitude"]
        assert 51.4 < lat < 51.6
        assert -0.2 < lng < 0.0

    @pytest.mark.asyncio
    async def test_outcode_geocode(self):
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OUTCODES_IO_URL}/SL6")
        assert resp.status_code == 200
        data = resp.json()
        lat = data["result"]["latitude"]
        lng = data["result"]["longitude"]
        assert 51.4 < lat < 51.6
        assert -0.8 < lng < -0.6

    @pytest.mark.asyncio
    async def test_invalid_postcode_returns_404(self):
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{POSTCODES_IO_URL}/NOTAPOSTCODE")
        assert resp.status_code == 404


class TestORSPelias:
    @pytest.mark.asyncio
    async def test_geocode_address(self):
        api_key = os.environ.get("HEIGIT_API_KEY", "")
        if not api_key:
            pytest.skip("HEIGIT_API_KEY not set")

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.openrouteservice.org/geocode/search",
                params={"text": "Shoppenhangers Road, Maidenhead, SL6, UK", "size": 1},
                headers={"Authorization": api_key},
            )
        assert resp.status_code == 200
        data = resp.json()
        features = data.get("features", [])
        assert len(features) > 0
        lng, lat = features[0]["geometry"]["coordinates"]
        assert 51.4 < lat < 51.6
        assert -0.8 < lng < -0.6


class TestExtractPostcodeEdgeCases:
    """Real-world address formats from Rightmove."""

    def test_maidenhead_address(self):
        pc = extract_postcode("Shoppenhangers Road, Maidenhead, SL6")
        assert pc == "SL6"

    def test_london_address_full_postcode(self):
        pc = extract_postcode("Victoria Street, London, SW1E 5JL")
        assert pc == "SW1E 5JL"

    def test_london_address_outcode(self):
        pc = extract_postcode("Whitechapel Road, London, E1")
        assert pc == "E1"

    def test_property_with_full_postcode(self):
        pc = extract_postcode("High Street, Oxford, OX1 4RP")
        assert pc == "OX1 4RP"
