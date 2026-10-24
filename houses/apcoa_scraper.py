"""APCOA parking data — scraping and URL generation for APCOA car parks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypedDict

from houses.apcoa_rates import city_slugs, extract_daily_rate_from_tariff, make_slug


class ApcoaPage(TypedDict):
    """A parsed APCOA page as the parking records carry it: name, address, price."""

    name: str | None
    address: str | None
    price: float


@dataclass(frozen=True)
class ApcoaPageRecord:
    """Wire shape of a parsed APCOA page: name, address, and price."""

    name: str | None
    address: str | None
    price: float

    def to_dict(self) -> ApcoaPage:
        return ApcoaPage(name=self.name, address=self.address, price=self.price)


def apcoa_location_urls(station_name: str) -> list[str]:
    """Candidate APCOA location page URLs for a station — the one URL-shape
    helper (the scraper class and the parking-rate sync both use it)."""
    station_slug = make_slug(station_name)
    urls: list[str] = []
    for city_slug in city_slugs(station_name):
        urls.append(f"{ApcoaScraper._APCOA_BASE}/{city_slug}/{station_slug}-station-{city_slug}")
        urls.append(f"{ApcoaScraper._APCOA_BASE}/{city_slug}/{city_slug}-station-{city_slug}")
    return urls


# lucidlint: ignore static-husk the class IS car_park's injected seam type — one parse surface (review-log)
class ApcoaScraper:
    """Scrape APCOA parking data: URL generation, location page parsing,
    and prebook listing parsing.

    Usage::

        scraper = ApcoaScraper()
        urls = scraper.apcoa_location_urls("Woking")
        result = scraper._parse_apcoa_location_page(page_text, title)
    """

    _APCOA_BASE = "https://www.apcoa.co.uk/find-parking/locations"
    # Plausibility bound for daily rates: APCOA daily parking at most £100.
    _MAX_PLAUSIBLE_DAILY_RATE = 100

    @staticmethod
    def _make_slug(name: str) -> str:
        """Convert a station name to an APCOA URL slug."""
        return make_slug(name)

    @staticmethod
    def _city_slugs(station_name: str) -> list[str]:
        """Generate candidate city slugs for APCOA URL construction."""
        return city_slugs(station_name)

    @staticmethod
    def apcoa_location_urls(station_name: str) -> list[str]:
        """Generate candidate APCOA location page URLs for a station."""
        return apcoa_location_urls(station_name)

    # ── APCOA page parsers (pure functions, testable with fixtures) ───

    @staticmethod
    def _parse_apcoa_location_page(page_text: str, page_title: str) -> ApcoaPageRecord | None:
        """Extract car park name, address, and price from an APCOA location page.

        The page has a "Pricing and payment" accordion open, with tariff
        text visible.  Returns an ``ApcoaPageRecord`` with ``name``,
        ``address``, and ``price`` fields, or ``None`` if parsing fails.
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
            # No "Parking offers nearby" marker on this page: cap the tariff
            # window at 3000 chars — a parse-window heuristic that bounds the
            # scan while still covering the whole tariff section of real pages.
            # lucidlint: ignore magic-number parse-window heuristic — tariff-scan cap (review-log)
            tariff_end = tariff_start + 3000
        tariff_text = page_text[tariff_start:tariff_end]

        price = extract_daily_rate_from_tariff(tariff_text)
        if price is None:
            return None
        if not (0 <= price <= ApcoaScraper._MAX_PLAUSIBLE_DAILY_RATE):
            return None

        return ApcoaPageRecord(
            name=name,
            address=address,
            price=round(price, 2),
        )

    @staticmethod
    def _parse_apcoa_prebook_listing(page_text: str) -> ApcoaPageRecord | None:
        """Extract name, address, and price from an APCOA prebook listing page.

        The page lists nearby car parks with "From £X.XX" prices.
        Returns an ``ApcoaPageRecord`` with ``name``, ``address``, and
        ``price`` fields, or ``None``.
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
        if not (0 <= cost <= ApcoaScraper._MAX_PLAUSIBLE_DAILY_RATE):
            return None

        return ApcoaPageRecord(
            name=name,
            address=address,
            price=round(cost, 2),
        )
