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
import re
from dataclasses import dataclass
from pathlib import Path

from money import Money

from houses.attempt import Attempt
from houses.stations import Station

logger = logging.getLogger(__name__)

_PARKING_RATES_PATH = Path("data/parking_rates.csv")

# ── APCOA URL helpers (mirrors scripts/sync_parking_rates.py) ─────

_APCOA_BASE = "https://www.apcoa.co.uk/find-parking/locations"


def _make_slug(name: str) -> str:
    """Convert a station name to an APCOA URL slug."""
    slug = name.lower()
    slug = slug.replace("'", "").replace("&", "and")
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _city_slugs(station_name: str) -> list[str]:
    """Generate candidate city slugs for APCOA URL construction."""
    name = station_name.strip()
    full = _make_slug(name)
    first = _make_slug(name.split()[0])
    candidates = [full]
    if first != full:
        candidates.append(first)
    return candidates


def _apcoa_location_urls(station_name: str) -> list[str]:
    """Generate candidate APCOA location page URLs for a station."""
    station_slug = _make_slug(station_name)
    urls: list[str] = []
    for city_slug in _city_slugs(station_name):
        urls.append(f"{_APCOA_BASE}/{city_slug}/{station_slug}-station-{city_slug}")
        urls.append(f"{_APCOA_BASE}/{city_slug}/{city_slug}-station-{city_slug}")
    return urls


# ── APCOA page parsers (pure functions, testable with fixtures) ───


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


class CarParkRegistry:
    """CSV-backed car park database.

    Instantiate per-request — no module-level globals.
    Loads ``data/parking_rates.csv`` lazily on first query.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, CarPark] | None = None
        self._by_crs: dict[str, CarPark] | None = None
        self._station_map: dict[str, str] = {}

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
        by_name: dict[str, CarPark] = {}
        by_crs: dict[str, CarPark] = {}
        for cp in car_parks:
            by_name[cp.name.lower()] = cp
        reg = cls.__new__(cls)
        reg._by_name = by_name
        reg._by_crs = by_crs
        reg._station_map = station_map or {}
        return reg

    # ── Loading ────────────────────────────────────────────────────

    def _load(self) -> None:
        """Parse ``data/parking_rates.csv`` into lookup dicts."""
        if self._by_name is not None:
            return
        by_name: dict[str, CarPark] = {}
        by_crs: dict[str, CarPark] = {}

        if not _PARKING_RATES_PATH.is_file():
            logger.warning("Parking rates file not found at %s", _PARKING_RATES_PATH)
            self._by_name = by_name
            self._by_crs = by_crs
            return

        with _PARKING_RATES_PATH.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw_name = (row.get("station_name") or "").strip()
                crs = (row.get("crs") or "").strip().upper()
                if not raw_name and not crs:
                    continue

                raw_cost = (row.get("daily_cost_gbp") or "").strip()
                cost: Money | None = None
                if raw_cost:
                    try:
                        cost = Money(raw_cost, "GBP")
                    except (ValueError, TypeError):
                        cost = None

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

    # ── APCOA lookup (location page + prebook listing fallback) ───

    async def load_costs(self, car_park: CarPark, station: Station) -> Attempt[CarPark]:
        """Look up the daily cost for a known car park via APCOA.

        Updates the car park in-memory and persists the result to CSV.
        Returns the updated ``CarPark`` wrapped in ``Attempt``.
        """
        result = await self._apcoa_lookup(station)
        if result is None:
            return Attempt.impossible("apcoa", f"No APCOA rate found for {station.name}")

        car_park.daily_cost = Money(str(result["price"]), "GBP")
        if result.get("address"):
            car_park.address = result["address"]
        if result.get("name"):
            car_park.name = result["name"]

        self._persist_results(station, car_park)
        return Attempt.succeeded(car_park, "apcoa")

    async def add_nearest_car_park_for(self, station: Station) -> Attempt[CarPark]:
        """Find the nearest APCOA car park for a station not in the CSV.

        Tries the station's APCOA location page first, then falls back
        to the prebook listing page.  Creates a ``CarPark``, persists
        to CSV, and returns it wrapped in ``Attempt``.
        """
        result = await self._apcoa_lookup(station)
        if result is None:
            return Attempt.impossible("apcoa", f"No APCOA car park found near {station.name}")

        car_park = CarPark(
            name=result.get("name") or f"{station.name} Station Car Park",
            daily_cost=Money(str(result["price"]), "GBP"),
            address=result.get("address"),
        )

        self._persist_results(station, car_park)
        return Attempt.succeeded(car_park, "apcoa")

    async def _apcoa_lookup(self, station: Station) -> dict | None:
        """Scrape APCOA for a car park near *station*.

        Strategy (matching ``scripts/sync_parking_rates.py``):
          1. Try the station's APCOA location page (e.g.
             ``/find-parking/locations/{city}/{name}-station-{city}``)
          2. Fall back to the prebook listing page near the station's
             coordinates.

        Returns dict with ``name``, ``address``, and ``price`` keys,
        or ``None`` if nothing found.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("playwright not installed — cannot scrape APCOA")
            return None

        async with async_playwright() as pw, await pw.chromium.launch(headless=True) as browser:
            page = await browser.new_page()

            # ── Strategy 1: APCOA location page ──────────────
            for url in _apcoa_location_urls(station.name):
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(2)

                    title = await page.title()
                    if "404" in title:
                        continue

                    # Dismiss privacy dialog
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

                    result = _parse_apcoa_location_page(page_text, title)
                    if result is not None:
                        logger.info(
                            "APCOA location page for '%s': %s = £%.2f",
                            station.name,
                            result.get("name", "?"),
                            result["price"],
                        )
                        return result
                except Exception as e:
                    logger.debug("APCOA location page failed for %s: %s", station.name, e)
                    continue

            # ── Strategy 2: Prebook listing page ─────────────
            lat, lng = station.location.lat, station.location.lon
            try:
                url = (
                    "https://prebook.apcoa.co.uk/locationsearch/nearestcarparks"
                    f"?latitude={lat}&longitude={lng}&placeName={station.name}&maximumDistance=3"
                )
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(3.5)

                # Dismiss privacy dialog
                await page.evaluate(
                    """() => {
                            const btns = [...document.querySelectorAll('button')];
                            const a = btns.find(b => b.textContent.includes('Agree always'));
                            if (a) a.click();
                        }"""
                )
                await asyncio.sleep(1)

                page_text = await page.evaluate("() => document.body.innerText")
                title = await page.title()

                # Check for "Sorry" — means no results
                if "Sorry" in page_text:
                    logger.info("APCOA prebook for '%s': no car parks found", station.name)
                    return None

                result = _parse_apcoa_prebook_listing(page_text)
                if result is not None:
                    logger.info(
                        "APCOA prebook for '%s': %s = £%.2f",
                        station.name,
                        result.get("name", "?"),
                        result["price"],
                    )
                    return result
            except Exception as e:
                logger.debug("APCOA prebook listing failed for %s: %s", station.name, e)

            logger.info("APCOA lookup for '%s': all strategies exhausted", station.name)
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
            cost_str = f"{float(car_park.daily_cost.amount):.2f}"

        if _PARKING_RATES_PATH.is_file():
            with _PARKING_RATES_PATH.open(newline="") as f:
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
        _PARKING_RATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _PARKING_RATES_PATH.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["station_name", "crs", "daily_cost_gbp", "car_park_name", "address"])
            writer.writerows(rows)

        # Force reload on next query
        self._by_name = None
        self._by_crs = None
