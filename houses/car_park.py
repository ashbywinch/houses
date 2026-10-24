"""Car park data — car parks with daily parking costs.

Each row in ``data/parking_rates.csv`` represents the nearest car park
to a station, with its daily cost. If the car park has its own name and
address they are stored too; otherwise it's assumed to be the station's
own car park and the name is derived from the station name.

Usage::

    parking = CarParkRegistry()
    car_park = parking.find_car_park(station)
    if car_park is None:
        result = await parking.add_nearest_car_park_for(station)
        ...
    elif car_park.daily_cost is None:
        result = await parking.load_costs(car_park, station)
        ...
"""

from __future__ import annotations

import asyncio
import csv
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from money import Money
from playwright.async_api import async_playwright

from dag.attempt import Attempt
from dag.guard import capture_async
from houses.apcoa_scraper import ApcoaPageRecord, ApcoaScraper
from houses.stations import Station

logger = logging.getLogger(__name__)

_PARKING_RATES_PATH = Path("data/parking_rates.csv")


@dataclass
class CarPark:
    """A car park with a daily parking cost.

    No station-specific fields — the relationship between car park and
    station is external (the ``CarParkRegistry`` manages it).

    ``daily_cost``:
        ``Money`` — known cost (even if £0 for free parking)
        ``None``  — cost not yet checked / unknown

    ``address``:
        Street address of the car park, if known (e.g. from APCOA).
        ``None`` means the car park is at the station.
    """

    name: str
    daily_cost: Money | None = None
    address: str | None = None


def _parse_cost(raw_cost: str) -> Money | None:
    """Parse a daily-cost string into GBP Money, or None if unparseable."""
    try:
        return Money(raw_cost, "GBP")
    except (ValueError, TypeError):
        return None


def _log_apcoa_find(result: ApcoaPageRecord, source: str, station: Station) -> ApcoaPageRecord:
    """Log a successful APCOA lookup and pass the result through."""
    logger.info(
        "APCOA %s for '%s': %s = £%.2f",
        source,
        station.name,
        result.name or "?",
        result.price,
    )
    return result


class ApcoaPageWalk:
    """The Playwright page walk over APCOA: the station's location page
    first, then the prebook listing near its coordinates.

    Owns the APCOA scraper and returns the ``ApcoaPageRecord`` it finds,
    or ``None``; the registry-facing ``ApcoaCarParkLookup`` writes
    results back.
    """

    _apcoa_scraper_inst: ApcoaScraper

    # Page-load settle times: the prebook results page hydrates its tariff
    # table slower than the location page.
    SETTLE_AFTER_LOCATION_PAGE_SECONDS = 2.0
    SETTLE_AFTER_PREBOOK_PAGE_SECONDS = 3.5

    @property
    def _apcoa_scraper(self) -> ApcoaScraper:
        """Lazily-initialized APCOA scraper."""
        if not hasattr(self, "_apcoa_scraper_inst"):
            self._apcoa_scraper_inst = ApcoaScraper()
        return self._apcoa_scraper_inst


    async def lookup(self, station: Station) -> ApcoaPageRecord | None:
        """Scrape APCOA for a car park near *station*.

        Strategy (matching ``scripts/sync_parking_rates.py``):
          1. Try the station's APCOA location page (e.g.
             ``/find-parking/locations/{city}/{name}-station-{city}``)
          2. Fall back to the prebook listing page near the station's
             coordinates.

        Returns an ``ApcoaPageRecord`` with ``name``, ``address``, and
        ``price`` fields, or ``None`` if nothing found.
        """
        async with async_playwright() as pw, await pw.chromium.launch(headless=True) as browser:
            page = await browser.new_page()

            for strategy in (self._try_location_pages, self._try_prebook_page):
                found = await strategy(page, station)
                if found is not None:
                    return found

            logger.info("APCOA lookup for '%s': all strategies exhausted", station.name)
            return None

    async def _try_location_pages(self, page, station: Station) -> ApcoaPageRecord | None:
        """Strategy 1: the station's APCOA location page, across candidate URLs."""
        for url in self._apcoa_scraper.apcoa_location_urls(station.name):
            found, failure = await capture_async(partial(self._read_location_page, page, url, station))
            if failure is not None:
                # Fail fast: re-raise the ORIGINAL exception so the DAG's
                # classifier can name the network type (a wrapper kills retry).
                logger.warning("APCOA lookup failed for %s (url=%s)", station.name, url)
                raise failure
            if found is not None:
                return found
        return None

    async def _read_location_page(self, page, url: str, station: Station) -> ApcoaPageRecord | None:
        """One candidate location page: scrape it, or None when it is absent."""
        await self._goto_and_settle(page, url, self.SETTLE_AFTER_LOCATION_PAGE_SECONDS)

        title = await page.title()
        if "404" in title:
            return None

        await self._dismiss_privacy_dialog(page)

        # Click "Pricing and payment" accordion
        await page.evaluate(
            """() => {
                    const btns = [...document.querySelectorAll('button')];
                    for (const b of btns) {
                        if (b.textContent.includes('Pricing and payment')) {
                            b.click(); return;
                        }
                    }
                }"""
        )
        await asyncio.sleep(2)

        page_text = await page.evaluate("() => document.body.innerText")
        record = self._apcoa_scraper._parse_apcoa_location_page(page_text, title)
        if record is None:
            return None
        return _log_apcoa_find(record, "location page", station)

    async def _try_prebook_page(self, page, station: Station) -> ApcoaPageRecord | None:
        """Strategy 2: the prebook listing page near the station's coordinates."""
        lat, lng = station.location.lat, station.location.lon
        url = (
            "https://prebook.apcoa.co.uk/locationsearch/nearestcarparks"
            f"?latitude={lat}&longitude={lng}&placeName={station.name}&maximumDistance=3"
        )
        found, failure = await capture_async(lambda: self._read_prebook_page(page, url, station))
        if failure is not None:
            logger.debug("APCOA prebook listing failed for %s: %s", station.name, failure)
            return None
        return found

    async def _read_prebook_page(self, page, url: str, station: Station) -> ApcoaPageRecord | None:
        """The prebook listing page's car parks, or None when it lists none."""
        await self._goto_and_settle(page, url, self.SETTLE_AFTER_PREBOOK_PAGE_SECONDS)

        await self._dismiss_privacy_dialog(page)

        page_text = await page.evaluate("() => document.body.innerText")

        # Check for "Sorry" — means no results
        if "Sorry" in page_text:
            logger.info("APCOA prebook for '%s': no car parks found", station.name)
            return None

        record = self._apcoa_scraper._parse_apcoa_prebook_listing(page_text)
        if record is None:
            return None
        return _log_apcoa_find(record, "prebook", station)

    @staticmethod
    async def _goto_and_settle(page, url: str, settle_seconds: float) -> None:
        """Open an APCOA page and let its content settle before scraping."""
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(settle_seconds)

    @staticmethod
    async def _dismiss_privacy_dialog(page) -> None:
        """Click the APCOA consent button when it appears — the location
        page offers 'Accept All', the prebook page 'Agree always'."""
        await page.evaluate(
            """() => {
                    const btns = [...document.querySelectorAll('button')];
                    for (const b of btns) {
                        const t = b.textContent.trim();
                        if (t.includes('Accept All') || t.includes('Agree always')) {
                            b.click(); return;
                        }
                    }
                }"""
        )
        await asyncio.sleep(1)


class ApcoaCarParkLookup:
    """Registry-facing half of the car-park database: look up an APCOA car
    park for a station and write the result back through the
    ``CarParkRegistry`` it is constructed with (in-memory mutation + CSV
    persistence), so the registry stays the single owner of car-park state.
    """

    def __init__(
        self,
        registry: CarParkRegistry,
        apcoa_lookup_fn: Callable[[Station], Awaitable[ApcoaPageRecord | None]] | None = None,
    ) -> None:
        """``apcoa_lookup_fn`` is a test seam — it defaults to the real
        APCOA page walk, so tests never monkeypatch module globals.
        """
        self._registry: CarParkRegistry = registry
        self._apcoa_lookup_fn: Callable[[Station], Awaitable[ApcoaPageRecord | None]] = (
            apcoa_lookup_fn or ApcoaPageWalk().lookup
        )

    async def load_costs(self, car_park: CarPark, station: Station) -> Attempt[CarPark]:
        """Look up the daily cost for a known car park via APCOA.

        Updates the car park in-memory and persists the result to CSV.
        Returns the updated ``CarPark`` wrapped in ``Attempt``.
        """
        result = await self._apcoa_lookup_fn(station)
        if result is None:
            return Attempt.impossible(f"No APCOA rate found for {station.name}")

        car_park.daily_cost = Money(str(result.price), "GBP")
        if result.address:
            car_park.address = result.address
        if result.name:
            car_park.name = result.name

        self._registry._persist_results(station, car_park)
        return Attempt.succeeded(car_park)

    async def add_nearest_car_park_for(self, station: Station) -> Attempt[CarPark]:
        """Find the nearest APCOA car park for a station not in the CSV.

        Tries the station's APCOA location page first, then falls back
        to the prebook listing page.  Creates a ``CarPark``, persists
        to CSV, and returns it wrapped in ``Attempt``.
        """
        result = await self._apcoa_lookup_fn(station)
        if result is None:
            return Attempt.impossible(f"No APCOA car park found near {station.name}")

        car_park = CarPark(
            name=result.name or f"{station.name} Station Car Park",
            daily_cost=Money(str(result.price), "GBP"),
            address=result.address,
        )

        self._registry._persist_results(station, car_park)
        return Attempt.succeeded(car_park)


class CarParkRegistry:
    """CSV-backed car park database.

    Instantiate per-request — no module-level globals.
    Loads ``data/parking_rates.csv`` lazily on first query.
    """

    def __init__(self, *, rates_path: Path | None = None) -> None:
        """``rates_path`` is a test seam — it defaults to the module CSV
        path, so tests never monkeypatch module globals.
        """
        self._by_name: dict[str, CarPark] | None = None
        self._by_crs: dict[str, CarPark] | None = None
        self._station_map: dict[str, str] = {}
        self._rates_path: Path = rates_path or _PARKING_RATES_PATH

    @classmethod
    def from_car_parks(cls, car_parks: list[CarPark], station_map: dict[str, str] | None = None) -> CarParkRegistry:
        """Create a registry pre-populated with ``CarPark`` objects.

        ``station_map`` maps station names (lowercase) to the car park
        name they should resolve to.  When omitted, each car park's
        own ``name`` is used as the station lookup key.

        Usage::

            registry = CarParkRegistry.from_car_parks(
                car_parks=[CarPark(name="Fleet", daily_cost=Money("10.90", "GBP"))],
                station_map={"fleet rail station": "Fleet"},
            )
        """
        by_name: dict[str, CarPark] = {cp.name.lower(): cp for cp in car_parks}
        by_crs: dict[str, CarPark] = {}
        reg = cls.__new__(cls)
        reg._by_name = by_name
        reg._by_crs = by_crs
        reg._station_map = station_map or {}
        reg._rates_path = _PARKING_RATES_PATH
        return reg

    # ── Loading ────────────────────────────────────────────────────

    def _load(self) -> None:
        """Parse ``data/parking_rates.csv`` into lookup dicts."""
        if self._by_name is not None:
            return
        by_name: dict[str, CarPark] = {}
        by_crs: dict[str, CarPark] = {}

        if not self._rates_path.is_file():
            logger.warning("Parking rates file not found at %s", self._rates_path)
            self._by_name = by_name
            self._by_crs = by_crs
            return

        with self._rates_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw_name = (row.get("station_name") or "").strip()
                crs = (row.get("crs") or "").strip().upper()
                if not raw_name and not crs:
                    continue

                raw_cost = (row.get("daily_cost_gbp") or "").strip()
                cost = _parse_cost(raw_cost) if raw_cost else None

                car_park_name = (row.get("car_park_name") or "").strip()
                if not car_park_name:
                    car_park_name = f"{raw_name} Station Car Park"

                address = (row.get("address") or "").strip() or None

                car_park = CarPark(
                    name=car_park_name,
                    daily_cost=cost,
                    address=address,
                )

                key = raw_name.lower()
                by_name[key] = car_park
                if crs:
                    by_crs[crs] = car_park

        self._by_name = by_name
        self._by_crs = by_crs

    # ── Lookup ─────────────────────────────────────────────────────

    def find_car_park(self, station: Station) -> CarPark | None:
        """Find the nearest car park to a station.

        Looks up by the station's canonical name first, then falls back
        to CRS code (matching the existing ``_lookup_parking_cost``
        behaviour).
        """
        self._load()
        if self._by_name is None:
            return None

        clean = station.name.lower() if station else ""
        mapped = self._station_map.get(clean, clean)
        car_park = self._by_name.get(mapped) if mapped else None
        if car_park is not None:
            return car_park

        crs = station.crs if station else None
        if crs and self._by_crs is not None:
            return self._by_crs.get(crs)

        return None

    # ── Persistence ────────────────────────────────────────────────

    def _persist_results(self, station: Station, car_park: CarPark) -> None:
        """Write a car park's details to the CSV and invalidate the in-memory cache.

        Adds a new row or updates an existing one matched by station name or CRS.
        """
        rows: list[list[str]] = []
        name_lower = station.name.lower()
        crs_upper = (station.crs or "").upper()
        found = False

        cost_str = ""
        if car_park.daily_cost is not None:
            cost_str = f"{car_park.daily_cost.amount:.2f}"

        if self._rates_path.is_file():
            with self._rates_path.open(newline="") as f:
                for row in csv.DictReader(f):
                    existing_name = (row.get("station_name") or "").strip().lower()
                    existing_crs = (row.get("crs") or "").strip().upper()
                    if existing_name == name_lower or existing_crs == crs_upper:
                        rows.append(
                            [
                                station.name,
                                crs_upper,
                                cost_str,
                                car_park.name,
                                car_park.address or "",
                            ]
                        )
                        found = True
                    else:
                        rows.append(
                            [
                                row.get("station_name", ""),
                                existing_crs,
                                row.get("daily_cost_gbp", ""),
                                row.get("car_park_name", ""),
                                row.get("address", ""),
                            ]
                        )

        if not found:
            rows.append(
                [
                    station.name,
                    crs_upper,
                    cost_str,
                    car_park.name,
                    car_park.address or "",
                ]
            )

        rows.sort(key=lambda r: r[1])
        self._rates_path.parent.mkdir(parents=True, exist_ok=True)
        with self._rates_path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["station_name", "crs", "daily_cost_gbp", "car_park_name", "address"])
            writer.writerows(rows)

        # Force reload on next query
        self._by_name = None
        self._by_crs = None
