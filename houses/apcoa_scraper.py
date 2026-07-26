"""APCOA parking data — scraping and URL generation for APCOA car parks."""

from __future__ import annotations

import re


class ApcoaScraper:
    """Scrape APCOA parking data: URL generation, location page parsing,
    and prebook listing parsing.

    Usage::

        scraper = ApcoaScraper()
        urls = scraper.apcoa_location_urls("Woking")
        result = scraper._parse_apcoa_location_page(page_text, title)
    """

    _APCOA_BASE = "https://www.apcoa.co.uk/find-parking/locations"

    @staticmethod
    def _make_slug(name: str) -> str:
        """Convert a station name to an APCOA URL slug."""
        slug = name.lower()
        slug = slug.replace("'", "").replace("&", "and")
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        return slug.strip("-")

    def _city_slugs(self, station_name: str) -> list[str]:
        """Generate candidate city slugs for APCOA URL construction."""
        name = station_name.strip()
        full = self._make_slug(name)
        first = self._make_slug(name.split()[0])
        candidates = [full]
        if first != full:
            candidates.append(first)
        return candidates

    def apcoa_location_urls(self, station_name: str) -> list[str]:
        """Generate candidate APCOA location page URLs for a station."""
        station_slug = self._make_slug(station_name)
        urls: list[str] = []
        for city_slug in self._city_slugs(station_name):
            urls.append(f"{self._APCOA_BASE}/{city_slug}/{station_slug}-station-{city_slug}")
            urls.append(f"{self._APCOA_BASE}/{city_slug}/{city_slug}-station-{city_slug}")
        return urls

    # ── APCOA page parsers (pure functions, testable with fixtures) ───

    @staticmethod
    def _parse_apcoa_location_page(page_text: str, page_title: str) -> dict | None:
        """Extract car park name, address, and price from an APCOA location page.

        The page has a "Pricing and payment" accordion open, with tariff
        text visible.  Returns dict with ``name``, ``address``, and
        ``price`` keys, or ``None`` if parsing fails.
        """
        # Name: from page title (e.g. "Bourne End Station - Bourne End - APCOA")
        name = page_title
        for suffix in (" - APCOA", " | APCOA"):
            if suffix in page_title:
                name = page_title.split(suffix)[0].strip()
                break

        # Address: find a line with a postcode near the car park name heading.
        # On APCOA pages the name line ends with "Off-street open" and the
        # next line is the address (e.g. "Station Road, SL8 5QH Bourne End").
        address: str | None = None
        lines = [ln.strip() for ln in page_text.split("\n")]
        for i, line in enumerate(lines):
            if "Off-street" in line and i + 1 < len(lines):
                candidate = lines[i + 1].strip()
                if candidate and re.search(r"[A-Z]{1,2}[0-9]", candidate):
                    address = candidate
                    break

        # Price: extract from the "Parking tariff" section
        tariff_start = page_text.find("Parking tariff")
        if tariff_start < 0:
            tariff_start = page_text.find("Pricing and payment")
        if tariff_start < 0:
            return None

        tariff_end = page_text.find("Parking offers nearby", tariff_start)
        if tariff_end < 0:
            tariff_end = tariff_start + 3000
        tariff_text = page_text[tariff_start:tariff_end]

        from scripts.sync_parking_rates import extract_daily_rate_from_tariff

        price = extract_daily_rate_from_tariff(tariff_text)
        if price is None:
            return None
        if not (0 <= price <= 100):
            return None

        return {"name": name, "address": address, "price": round(price, 2)}

    @staticmethod
    def _parse_apcoa_prebook_listing(page_text: str) -> dict | None:
        """Extract name, address, and price from an APCOA prebook listing page.

        The page lists nearby car parks with "From £X.XX" prices.
        Returns dict with ``name``, ``address``, ``price`` or ``None``.
        """
        lines = [ln.strip() for ln in page_text.split("\n") if ln.strip()]
        name: str | None = None
        address: str | None = None
        price: str | None = None

        for i, line in enumerate(lines):
            m = re.search(r"From\s*£(\d+\.\d{2})", line, re.IGNORECASE)
            if m:
                price = m.group(1)
                # Name is typically 2-3 lines above the "From £X" line
                if i >= 2:
                    name = lines[i - 2]
                if i >= 1:
                    address = lines[i - 1]
                break

        if price is None:
            return None

        cost = float(price)
        if not (0 <= cost <= 100):
            return None

        return {"name": name, "address": address, "price": round(cost, 2)}
