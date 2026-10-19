"""Sync parking rates from APCOA location pages via Playwright.

Navigates to each station's APCOA location page, clicks the "Pricing
and payment" accordion, and extracts the Monday-Friday Daily Rate.

Usage:
    uv run python scripts/sync_parking_rates.py
    uv run python scripts/sync_parking_rates.py --crs WOK,MAI
    uv run python scripts/sync_parking_rates.py --missing
    uv run python scripts/sync_parking_rates.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
from pathlib import Path
from typing import TypedDict

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from dag.guard import capture_async

logger = logging.getLogger(__name__)

class _ApcoaRates:
    """The APCOA parking-rates source: data paths, base URL, and scrape pacing."""

    STATIONS_CSV = Path("data/stations.csv")
    PARKING_CSV = Path("data/parking_rates.csv")
    MAX_COST_GBP = 100.0
    APCOA_BASE = "https://www.apcoa.co.uk/find-parking/locations"
    REQUEST_DELAY_SECONDS = 3.0
    CHECKPOINT_INTERVAL = 5


type _StationPosition = tuple[float, float]
"""A station's (latitude, longitude) as stations.csv states them."""


class _StationRecord(TypedDict):
    """One station as this script's CSV boundary carries it: the stations.csv
    row's station name and CRS code, projected for the parking-rates CSV."""

    stationName: str
    crs: str


def extract_daily_rate_from_tariff(tariff_text: str) -> float | None:
    """Extract the weekday daily parking rate from APCOA tariff text.

    Handles several formats:
    - "Daily Rate: £X.XX" (Woking, Fleet)
    - "Daily Rate before 12pm: £X.XX" (Bourne End)
    - "Up to 24 hours £X.XX" (Didcot Foxhall)
    - Multi-line tariff from the Monday-Friday section.

    Returns:
        float — the daily rate in GBP
        None — if no rate could be confidently extracted

    Designed as a pure function — no I/O, no side effects. Testable
    with fixture files containing ``Parking tariff`` section text.
    """
    # The caller already extracts the relevant section from the page.
    # The text may or may not start with "Parking tariff".
    tariff_start = tariff_text.find("Parking tariff")
    if tariff_start < 0:
        tariff_start = tariff_text.find("Pricing and payment")
    if tariff_start < 0:
        return None
    tariff = tariff_text[tariff_start:]

    # Isolate the Monday-Friday block (ends before Saturday or weeklies).
    # "Sunday" is too broad (matches "Monday - Sunday"), so we only
    # anchor on "Saturday" which cleanly separates weekday from weekend.
    mf_end = len(tariff)
    for marker in ["Saturday", "Weekly\n", "Monthly\n", "Season Ticket"]:
        idx = tariff.find(marker)
        if idx >= 0 and idx < mf_end:
            mf_end = idx
    mf = tariff[:mf_end]

    # Check for "Permit Holders Only" — no daily rate available
    if re.search(r"Permit\s*Holders?\s*Only", mf, re.IGNORECASE):
        return None

    # Strategy 1 then 2: explicit "Daily Rate: £X.XX" (handles "before 12pm"
    # qualifiers), then "Up to 24 hours £X.XX" / "Up to 24 hours: £X.XX".
    for pattern in (
        r"Daily\s*Rate\s*\S*?\s*:?\s*£(\d+\.\d{2})",
        r"(?:Up\s*to\s*)?24\s*hours?\s*:?\s*£(\d+\.\d{2})",
    ):
        m = re.search(pattern, mf, re.IGNORECASE)
        if m:
            return float(m.group(1))

    # Strategy 3: extract all £X.XX prices, take the highest (likely daily max)
    prices = [float(p) for p in re.findall(r"£(\d+\.\d{2})", mf)]
    if prices:
        max_price = max(prices)
        if max_price > 0:
            return max_price

    return None


def make_slug(name: str) -> str:
    """Convert a station name to an APCOA URL slug."""
    slug = name.lower()
    slug = slug.replace("'", "").replace("&", "and")
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def city_slugs(station_name: str) -> list[str]:
    """Generate candidate city slugs for APCOA URL construction.

    Tries the full station name first (e.g. 'Bourne End' →
    'bourne-end'), then falls back to the first word
    (e.g. 'Didcot Parkway' → 'didcot').
    """
    name = station_name.strip()
    full = make_slug(name)
    first = make_slug(name.split()[0])
    candidates = [full]
    if first != full:
        candidates.append(first)
    return candidates




def load_stations() -> list[_StationRecord]:
    stations: list[_StationRecord] = []
    with _ApcoaRates.STATIONS_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            crs = (row.get("crsCode", "") or "").strip()
            name = (row.get("stationName", "") or "").strip()
            if crs and name:
                stations.append(_StationRecord(stationName=name, crs=crs.upper()))
    logger.info("Loaded %d stations from %s", len(stations), _ApcoaRates.STATIONS_CSV)
    return stations

def load_existing_rates() -> dict[str, float | None]:
    """Return {crs: cost} from existing CSV."""
    rates: dict[str, float | None] = {}
    if not _ApcoaRates.PARKING_CSV.is_file():
        return rates
    with _ApcoaRates.PARKING_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            crs = (row.get("crs", "") or "").strip().upper()
            raw = (row.get("daily_cost_gbp", "") or "").strip()
            if not crs:
                continue
            if raw:
                try:
                    rates[crs] = float(raw)
                except ValueError:
                    rates[crs] = None
            else:
                rates[crs] = None
    return rates


def write_rates(all_stations: list[_StationRecord], rates: dict[str, float | None]) -> None:
    _ApcoaRates.PARKING_CSV.parent.mkdir(parents=True, exist_ok=True)
    with _ApcoaRates.PARKING_CSV.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["station_name", "crs", "daily_cost_gbp"])
        for s in sorted(all_stations, key=lambda x: x["crs"]):
            crs = s["crs"]
            cost = rates.get(crs)
            val = f"{cost:.2f}" if cost is not None else ""
            writer.writerow([s["stationName"], crs, val])
    logger.info("Wrote %d rates to %s", len(rates), _ApcoaRates.PARKING_CSV)


async def find_station_urls(page, station_name: str) -> list[str]:
    """Find station car park URLs from an APCOA city listing page.

    Tries each candidate city slug. Navigates to the city page and
    extracts links containing '-station-'.
    """
    for city_slug in city_slugs(station_name):
        listing_url = f"{_ApcoaRates.APCOA_BASE}/{city_slug}/"
        try:
            await page.goto(listing_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            if "404" in await page.title():
                logger.info("City listing page 404s for '%s'", city_slug)
                continue
            links: list[str] = await page.evaluate("""
                () => [...document.querySelectorAll('a')]
                    .map(a => a.href)
                    .filter(h => h.includes('/find-parking/locations/') && h.includes('-station-'))
            """)
            station_urls = list(set(links))
            if station_urls:
                logger.info("Found %d station URLs in %s", len(station_urls), listing_url)
                return station_urls
        # Playwright's Error covers goto/title/evaluate — one slug degrades, the loop continues.
        except PlaywrightError as e:
            logger.warning("Failed to fetch city listing %s: %s", listing_url, e)
            continue
    return []


async def extract_daily_rate(page, url: str) -> float | None:
    """Navigate to an APCOA location page and extract the daily rate.

    Clicks the "Pricing and payment" accordion button, then reads
    the "Daily Rate: £X.XX" from the tariff table.
    """
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        title = await page.title()
        if "404" in title:
            return None

        # Dismiss privacy dialog that might overlay the page
        await page.evaluate("""
            () => {
                const btns = [...document.querySelectorAll('button')];
                const accept = btns.find(b => b.textContent.includes('Accept All')
                    || b.textContent.includes('Agree always'));
                if (accept) accept.click();
            }
        """)
        await asyncio.sleep(1)

        # Click "Pricing and payment" to expand the tariff section
        await page.evaluate("""
            () => {
                const btns = [...document.querySelectorAll('button')];
                const target = btns.find(b => b.textContent.includes('Pricing and payment'));
                if (target) { target.click(); return true; }
                return false;
            }
        """)
        await asyncio.sleep(2)

        # Extract tariff text from the rendered page.
        tariff_text = await page.evaluate("""
            () => {
                const text = document.body.innerText;
                // Try "Parking tariff" first, then fall back to area after "Pricing and payment"
                let start = text.indexOf('Parking tariff');
                if (start < 0) {
                    const pp = text.indexOf('Pricing and payment');
                    if (pp >= 0) start = pp;
                }
                if (start < 0) return '';
                const end = text.indexOf('Parking offers nearby');
                return end < 0 ? text.slice(start) : text.slice(start, end);
            }
        """)
        if not tariff_text:
            # Debug: log what the page looks like
            debug_text = await page.evaluate("""() => {
                const text = document.body.innerText;
                const pi = text.indexOf('Pricing');
                const di = text.indexOf('Daily');
                return JSON.stringify({
                    hasPricingSection: pi >= 0,
                    hasDailyRate: di >= 0,
                    pricingContext: pi >= 0 ? text.substring(pi, pi + 300) : null,
                    dailyContext: di >= 0 ? text.substring(di, di + 100) : null,
                    buttons: [...document.querySelectorAll('button')]
                        .map(b => b.textContent.trim())
                        .filter(t => t.length > 0 && t.length < 50)
                        .slice(0, 10)
                });
            }""")
            logger.warning("No tariff section found on %s. Debug: %s", url, debug_text)
            return None

        cost = extract_daily_rate_from_tariff(tariff_text)
        if cost is None:
            logger.warning("Could not extract daily rate from %s", url)
            return None

        if cost < 0 or cost > _ApcoaRates.MAX_COST_GBP:
            logger.warning("Implausible rate £%.2f on %s", cost, url)
            return None

        station_name = url.split("/")[-1].replace("-", " ").title()
        logger.info("  → %s: £%.2f", station_name, cost)
        return round(cost, 2)

    # Playwright's Error covers goto/title/evaluate — one URL degrades to None, the caller tries the next.
    except PlaywrightError as e:
        logger.warning("Failed to extract rate from %s: %s", url, e)
        return None


async def _try_urls(page, urls) -> float | None:
    """Return the first rate found across candidate URLs, else None."""
    for url in urls:
        rate = await extract_daily_rate(page, url)
        if rate is not None:
            return rate
    return None


def _station_coords(station_name: str) -> _StationPosition | None:
    csv_path = Path("data/stations.csv")
    if not csv_path.is_file():
        return None
    with csv_path.open(newline="") as f:
        for row in csv.DictReader(f):
            if row.get("stationName", "").strip().lower() == station_name.strip().lower():
                lat_str = row.get("lat", "")
                lng_str = row.get("long", "")
                if lat_str and lng_str:
                    return float(lat_str), float(lng_str)
                return None
    return None


async def find_station_rate(page, station_name: str) -> float | None:
    """Find the daily parking rate for a station from APCOA.

    Tries candidate direct URLs first. If none work, falls back
    to the city listing page to find station car park URLs.
    """
    # houses.apcoa_scraper imports this module's slug helpers, so importing it
    # at module level would close the cycle — the URL-shape helper comes in here.
    # lucidlint: ignore inline-import deferred to break the houses↔scripts cycle (review-log)
    from houses.apcoa_scraper import apcoa_location_urls

    # Try direct URLs first
    rate = await _try_urls(page, apcoa_location_urls(station_name))
    if rate is not None:
        return rate

    # Fall back: check city listing page for more specific URLs
    station_urls = await find_station_urls(page, station_name)
    rate = await _try_urls(page, station_urls)
    if rate is not None:
        return rate

    # Last resort: the APCOA prebook listing page near the station. A failure
    # here is the station's failure — the caller's boundary records None and
    # the sync continues with the next station.
    coords = _station_coords(station_name)
    if coords is not None:
        lat, lng = coords
        listing_url = (
            f"https://prebook.apcoa.co.uk/locationsearch/nearestcarparks"
            f"?latitude={lat}&longitude={lng}&placeName={station_name}&maximumDistance=3"
        )
        logger.info("Trying APCOA prebook listing for '%s'...", station_name)
        rate = await extract_daily_rate(page, listing_url)
        if rate is not None:
            # Check that the page actually showed results (not just "Sorry!")
            page_text = await page.evaluate("() => document.body.innerText.substring(0, 300)")
            if "Sorry" not in page_text:
                return rate

    return None


async def _process_station(page, name: str, crs: str, rate_map: dict[str, float | None]) -> None:
    """Fetch and record one station's daily rate; failures record None.

    One station is one unit of work: any failure is logged and recorded as
    None, and the sync continues with the rest.
    """
    rate, failure = await capture_async(lambda: find_station_rate(page, name))
    if failure is not None:
        logger.warning("Failed to process %s (%s): %s", name, crs, failure)
        rate_map[crs] = None
        return
    rate_map[crs] = rate
    if rate is None:
        logger.info("  → %s: no rate found", name)


def _select_stations(args, all_stations: list[_StationRecord]) -> list[_StationRecord]:
    if args.crs:
        target = {c.strip().upper() for c in args.crs.split(",")}
        return [s for s in all_stations if s["crs"] in target]
    return list(all_stations)


def _filter_missing(
    args,
    stations: list[_StationRecord],
    existing: dict[str, float | None],
) -> list[_StationRecord]:
    if not args.missing:
        return stations
    filtered = [s for s in stations if s["crs"] not in existing or existing.get(s["crs"]) is None]
    logger.info("Filtered to %d missing stations", len(filtered))
    return filtered


# lucidlint: ignore latent-class main is the one-shot sync's thin CLI shell — threads args, no reuse axis (review-log)
async def main():
    parser = argparse.ArgumentParser(description="Sync parking rates from APCOA via Playwright")
    parser.add_argument("--crs", help="Comma-separated CRS codes to process")
    parser.add_argument("--missing", action="store_true", help="Only stations not yet in CSV")
    parser.add_argument("--force", action="store_true", help="Re-process stations already in CSV")
    args = parser.parse_args()
    all_stations = load_stations()

    stations = _select_stations(args, all_stations)
    existing = load_existing_rates()
    stations = _filter_missing(args, stations, existing)

    # Build the master rate map from existing + new
    rate_map: dict[str, float | None] = dict(existing)

    async with async_playwright() as pw, await pw.chromium.launch(headless=True) as browser:
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        processed = 0
        for s in stations:
            crs = s["crs"]
            name = s["stationName"]

            if not args.force and crs in rate_map and rate_map[crs] is not None:
                logger.info("Skipping %s (%s) — already have £%.2f", name, crs, rate_map[crs])
                continue

            logger.info("Processing %s (%s)...", name, crs)
            if processed > 0:
                await asyncio.sleep(_ApcoaRates.REQUEST_DELAY_SECONDS)
            await _process_station(page, name, crs, rate_map)

            processed += 1
            if processed % _ApcoaRates.CHECKPOINT_INTERVAL == 0:
                write_rates(all_stations, rate_map)

    write_rates(all_stations, rate_map)
    found = sum(1 for v in rate_map.values() if v is not None)
    logger.info("Done. Processed %d stations. %d have rates.", processed, found)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(main())

