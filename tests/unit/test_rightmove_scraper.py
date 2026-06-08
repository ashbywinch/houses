from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from houses.config import settings
from houses.rightmove_scraper import CACHE_DIR as SCRAPER_CACHE
from houses.rightmove_scraper import _parse_html, scrape

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SAMPLE_HTML = FIXTURES / "rightmove_sample.html"

SAMPLE_URL = "https://www.rightmove.co.uk/properties/123456789"


class TestParseHtml:
    def test_extracts_all_fields(self):
        html = SAMPLE_HTML.read_text(encoding="utf-8")
        result = _parse_html(html, SAMPLE_URL)

        assert result["address"] == "Foxhall Road, Didcot, OX11"
        assert result["postcode"] == "OX11 7EB"
        assert result["bedrooms"] == 5
        assert result["price"] == 650000.0
        assert result["latitude"] == 51.61074
        assert result["longitude"] == -1.25238

    def test_returns_empty_dict_for_empty_html(self):
        assert _parse_html("", SAMPLE_URL) == {}

    def test_returns_empty_dict_for_garbage(self):
        assert _parse_html("<html>garbage</html>", SAMPLE_URL) == {}

    def test_missing_json_ld_still_parses_preloaded_state(self):
        """When JSON-LD is removed, the preloaded state fallback still works."""
        html = """
        <html>
        <script>
        window.__PRELOADED_STATE__ = {
          "propertyData": {
            "id": 123,
            "bedrooms": 3,
            "price": 450000,
            "address": "456 Other Road, Other Town, OT1 1AA",
            "location": {
              "latitude": 52.0,
              "longitude": -1.0
            }
          }
        };
        </script>
        </html>
        """
        result = _parse_html(html, SAMPLE_URL)
        assert result["address"] == "456 Other Road, Other Town, OT1 1AA"
        assert result["bedrooms"] == 3
        assert result["price"] == 450000.0
        assert result["latitude"] == 52.0
        assert result["longitude"] == -1.0

    def test_invalid_json_in_ld_does_not_crash(self):
        html = '<script type="application/ld+json">{invalid</script>'
        result = _parse_html(html, SAMPLE_URL)
        assert isinstance(result, dict)

    def test_non_numeric_price_is_parsed(self):
        html = """
        <html>
        <script type="application/ld+json">
        {
          "@type": "Product",
          "offers": { "price": "\\u00a3425,000" }
        }
        </script>
        </html>
        """
        result = _parse_html(html, SAMPLE_URL)
        assert result["price"] == 425000.0


class TestScrapeWithSamplePage:
    @pytest.mark.asyncio
    async def test_uses_sample_page_when_configured(self):
        original = settings.rightmove_sample_page
        settings.rightmove_sample_page = str(SAMPLE_HTML)
        try:
            result = await scrape(SAMPLE_URL)
            assert result["address"] == "Foxhall Road, Didcot, OX11"
            assert result["bedrooms"] == 5
            assert result["price"] == 650000.0
        finally:
            settings.rightmove_sample_page = original

    @pytest.mark.asyncio
    async def test_sample_page_not_found_returns_empty(self):
        original = settings.rightmove_sample_page
        settings.rightmove_sample_page = "/nonexistent/file.html"
        try:
            result = await scrape(SAMPLE_URL)
            assert result == {}
        finally:
            settings.rightmove_sample_page = original

    def test_scrape_uses_cache_when_present(self):
        """scrape() reads from the page cache, ignoring the sample page."""
        original = settings.rightmove_sample_page
        try:
            SCRAPER_CACHE.mkdir(parents=True, exist_ok=True)
            cache_file = SCRAPER_CACHE / "99999999.html"
            cache_file.write_text(SAMPLE_HTML.read_text(encoding="utf-8"))
            settings.rightmove_sample_page = ""

            result = asyncio.run(scrape("https://www.rightmove.co.uk/properties/99999999"))
            assert result["address"] == "Foxhall Road, Didcot, OX11"
        finally:
            settings.rightmove_sample_page = original
            if cache_file.exists():
                cache_file.unlink()

    def test_scrape_offline_returns_empty_for_uncached(self):
        """scrape() returns empty for an uncached RID when offline mode is on.
        The conftest sets offline mode by default; this tests the fail-fast path."""
        original = settings.rightmove_sample_page
        settings.rightmove_sample_page = ""
        try:
            result = asyncio.run(scrape("https://www.rightmove.co.uk/properties/00000000"))
            assert result == {}, f"Expected empty dict, got {result}"
        finally:
            settings.rightmove_sample_page = original

    @pytest.mark.asyncio
    async def test_unknown_url_rid_returns_empty(self):
        original = settings.rightmove_sample_page
        settings.rightmove_sample_page = str(SAMPLE_HTML)
        try:
            result = await scrape("https://example.com/no-rid-here")
            assert result == {}
        finally:
            settings.rightmove_sample_page = original
