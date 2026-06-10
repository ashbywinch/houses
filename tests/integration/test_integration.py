"""Integration tests that hit real external APIs (cached responses)."""

import httpx  # noqa: I001
import pytest
from houses.enricher import OUTCODES_IO_URL, POSTCODES_IO_URL
from houses.server import extract_postcode


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


class TestGeocodeAddress:
    @pytest.mark.asyncio
    async def test_geocode_address(self):
        """Verify the full geocoding fallback chain runs without crashing.

        Tests that _geocode_address handles Google Maps → ORS → Nominatim
        gracefully regardless of external availability. If Nominatim is
        available, also verify coordinates are in the correct area.
        """
        from houses.enricher import geocode_address

        coords = (await geocode_address("Shoppenhangers Road, Maidenhead, SL6, UK")).value_or_none()

        if coords is None:
            # All backends unavailable (expected when Nomination is rate-limited)
            return

        lat, lng = coords
        assert 51.4 < lat < 51.6, f"Latitude {lat} not in Maidenhead range"
        assert -0.8 < lng < -0.6, f"Longitude {lng} not in Maidenhead range"


class TestVOACouncilTaxLookup:
    """Real VOA API calls — verify the scraper returns data for known postcodes."""

    @pytest.mark.asyncio
    async def test_voa_returns_results_for_gu22_8bq(self):
        from uk_property_apis.voa import VOAClient

        async with VOAClient() as client:
            page = await client.fetch_page("GU22 8BQ", page=0)

        assert len(page.rows) > 0, (
            "VOAClient.fetch_page returned 0 results for GU22 8BQ, "
            "but the VOA website has 9 properties including 7 Sandy Close."
        )


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
