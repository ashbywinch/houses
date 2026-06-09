"""Transit commute, petrol cost, and school lookup logic.

Uses TfL Unified API for transit routing, OpenRouteService for
driving distances, and UK government GIAS school data.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import logging
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from houses.api_cache import cached_async_client, get_cached, set_cached
from houses.config import settings
from houses.models import CommuteBreakdown, PetrolCost, SchoolInfo, TransitInfo
from houses.retry import retry_async

logger = logging.getLogger(__name__)


# Per-process-run API exhaustion tracking.
# Set when an API returns a usage-limit error so subsequent calls
# skip straight to the fallback instead of hammering the dead endpoint.
class _APIState:
    places_exhausted: bool = False
    ors_geo_exhausted: bool = False
    nominatim_exhausted: bool = False
    nominatim_last_call: float = 0.0


_api_state = _APIState()

POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"
OUTCODES_IO_URL = "https://api.postcodes.io/outcodes"
TFL_JOURNEY_URL = "https://api.tfl.gov.uk/Journey/JourneyResults"
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"

# ---------------------------------------------------------------------------
# API response cache helpers
# ---------------------------------------------------------------------------


async def _cached_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    *,
    max_retries: int = 0,
) -> dict | None:
    """GET with disk-backed JSON caching. Returns the parsed JSON or ``None``."""
    cached = get_cached("GET", url, params)
    if cached is not None:
        return cached

    async def _fetch():
        return await client.get(url, params=params)

    resp = await retry_async(_fetch, max_retries=max_retries, base_delay=0.5) if max_retries else await _fetch()
    resp.raise_for_status()
    data = resp.json()
    set_cached("GET", url, params, None, data)
    return data


async def _cached_post(
    client: httpx.AsyncClient,
    url: str,
    json_body: dict | None = None,
    headers: dict | None = None,
    *,
    max_retries: int = 0,
) -> dict | None:
    """POST with disk-backed JSON caching. Returns the parsed JSON or ``None``."""
    body_str = json.dumps(json_body, sort_keys=True) if json_body else None
    cached = get_cached("POST", url, None, body_str)
    if cached is not None:
        return cached

    async def _fetch():
        return await client.post(url, json=json_body, headers=headers)

    resp = await retry_async(_fetch, max_retries=max_retries, base_delay=0.5) if max_retries else await _fetch()
    resp.raise_for_status()
    data = resp.json()
    set_cached("POST", url, None, body_str, data)
    return data


# Full postcode: "SL6 1AA", outcode: "SL6"
_OUTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")
# Trailing postcode in address strings (e.g. ", SL6" or ", GU22 8BQ")
_END_PC_RE = re.compile(r",\s*[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?\s*$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Transit — TfL Unified API (free, no expiring trial)
# ---------------------------------------------------------------------------


def _next_weekday_date_params() -> dict[str, str]:
    """Return ``date`` and ``time`` params for the next upcoming weekday at 09:00."""
    now = datetime.now()
    if now.weekday() < 5 and now.hour < 9:
        return {"date": now.strftime("%Y%m%d"), "time": "0900"}
    target = now + timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return {"date": target.strftime("%Y%m%d"), "time": "0900"}


_STATION_SUFFIXES = [" Rail Station", " Underground Station", " Rail Station", " Station"]


def _shorten_station(name: str) -> str:
    for suffix in _STATION_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    if name.startswith("London "):
        name = name[7:]
    return name


_STATIONS_CSV = Path("data/stations.csv")
_BUS_FARES_PATH = Path("data/bus_fares.json")
_bus_fares_data: dict | None = None


def _load_bus_fares() -> dict | None:
    global _bus_fares_data
    if _bus_fares_data is not None:
        return _bus_fares_data
    if not _BUS_FARES_PATH.is_file():
        logger.warning("Bus fares file not found at %s", _BUS_FARES_PATH)
        _bus_fares_data = {}
        return _bus_fares_data
    with _BUS_FARES_PATH.open() as f:
        _bus_fares_data = json.load(f)
    return _bus_fares_data


def _clean_station_name_for_matching(name: str) -> str:
    """Strip station suffixes for CRS lookup. Does NOT strip 'London ' prefix."""
    name = name.replace("'", "").replace("\u2019", "")
    lower = name.lower()
    for suffix in [" rail station", " underground station", " station"]:
        if lower.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.strip()


def _lookup_station_crs(station_name: str) -> str | None:
    """Find CRS code for a station by exact case-insensitive match.

    Returns CRS code or None if not found.
    """
    if not _STATIONS_CSV.is_file():
        return None
    clean = _clean_station_name_for_matching(station_name).strip().lower()
    if not clean:
        return None
    with _STATIONS_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            if row.get("stationName", "").strip().lower() == clean:
                return (row.get("crsCode", "") or "").strip() or None
    logger.error("Station '%s' not found in stations.csv (cleaned: '%s')", station_name, clean)
    return None


_PARKING_RATES_PATH = Path("data/parking_rates.csv")
_parking_rates_cache: dict[str, float | None] | None = None


_ParkingRates = tuple[dict[str, float | None], dict[str, float | None]]


def _load_parking_rates() -> _ParkingRates:
    """Load parking rates from CSV into (name_keyed, crs_keyed) dicts."""
    global _parking_rates_cache
    if _parking_rates_cache is not None:
        return _parking_rates_cache
    by_name: dict[str, float | None] = {}
    by_crs: dict[str, float | None] = {}
    if not _PARKING_RATES_PATH.is_file():
        logger.warning("Parking rates file not found at %s", _PARKING_RATES_PATH)
        _parking_rates_cache = (by_name, by_crs)
        return _parking_rates_cache
    with _PARKING_RATES_PATH.open(newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("station_name", "") or "").strip().lower()
            crs = (row.get("crs", "") or "").strip().upper()
            raw = (row.get("daily_cost_gbp", "") or "").strip()
            if not name and not crs:
                continue
            val: float | None = None
            if raw:
                try:
                    val = float(raw)
                except ValueError:
                    val = None
            if name:
                by_name[name] = val
            if crs:
                by_crs[crs] = val
    _parking_rates_cache = (by_name, by_crs)
    return _parking_rates_cache


def _add_parking_rate_to_csv(station_name: str, crs: str, cost: float | None) -> None:
    """Add or update a parking rate in the CSV and refresh the cache."""
    global _parking_rates_cache
    rows: list[list[str]] = []
    name_lower = station_name.strip().lower()
    found = False
    if _PARKING_RATES_PATH.is_file():
        with _PARKING_RATES_PATH.open(newline="") as f:
            for row in csv.DictReader(f):
                existing_name = (row.get("station_name", "") or "").strip().lower()
                existing_crs = (row.get("crs", "") or "").strip().upper()
                if existing_name == name_lower or existing_crs == crs.upper():
                    rows.append([station_name, crs.upper(), f"{cost:.2f}" if cost is not None else ""])
                    found = True
                else:
                    rows.append([row.get("station_name", ""), existing_crs, row.get("daily_cost_gbp", "")])
    if not found:
        rows.append([station_name, crs.upper(), f"{cost:.2f}" if cost is not None else ""])
    rows.sort(key=lambda r: r[1])
    _PARKING_RATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _PARKING_RATES_PATH.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["station_name", "crs", "daily_cost_gbp"])
        writer.writerows(rows)
    _parking_rates_cache = None


async def _apcoa_prebook_lookup(station_name: str) -> float | None:
    """Try to find a nearby APCOA car park via the prebook listing page.

    Uses Playwright to render the JavaScript-dependent listing. Only called
    as a fallback when the station isn't in the parking_rates.csv cache.
    """
    coords = _lookup_station_coords(station_name)
    if coords is None:
        return None
    lat, lng = coords

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            async with await pw.chromium.launch(headless=True) as browser:
                page = await browser.new_page()
                url = (
                    f"https://prebook.apcoa.co.uk/locationsearch/nearestcarparks"
                    f"?latitude={lat}&longitude={lng}&placeName={station_name}&maximumDistance=3"
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

                # Extract the first car park's price
                rate_str = await page.evaluate(
                    """() => {
                        const text = document.body.innerText;
                        const m = text.match(/From[\\s\\S]*?£(\\d+\\.\\d{2})/i);
                        return m ? m[1] : null;
                    }"""
                )

            if rate_str:
                cost = float(rate_str)
                if 0 <= cost <= 100:
                    logger.info("APCOA prebook fallback for '%s': £%.2f", station_name, cost)
                    return round(cost, 2)

            logger.info("APCOA prebook fallback for '%s': no rate found", station_name)
            return None
    except Exception as e:
        logger.warning("APCOA prebook lookup failed for '%s': %s", station_name, e)
        return None


async def _lookup_parking_cost(station_name: str) -> float | None:
    """Daily parking cost at a station.

    Tries the parking_rates.csv cache first. When the station isn't found,
    falls back to searching the APCOA prebook listing page via Playwright.
    Any newly discovered rate is cached to the CSV for future lookups.

    Returns:
        0.0 = known free parking
        None = couldn't find cost
        float = daily parking cost in GBP
    """
    by_name, by_crs = _load_parking_rates()
    clean = _clean_station_name_for_matching(station_name).strip().lower()
    val = by_name.get(clean)
    if val is not None or clean in by_name:
        logger.debug("parking: '%s' -> '%s' = £%s (CSV hit)", station_name, clean, val)
        return val
    crs = _lookup_station_crs(station_name)
    if crs and crs in by_crs:
        logger.debug("parking: '%s' CRS=%s = £%s (CSV hit)", station_name, crs, by_crs[crs])
        return by_crs[crs]

    logger.debug("parking: '%s' not in CSV — trying APCOA prebook fallback", station_name)
    cost = await _apcoa_prebook_lookup(station_name)
    crs = crs or ""
    if cost is not None or (crs and clean):
        _add_parking_rate_to_csv(station_name, crs, cost)
    logger.debug("parking: '%s' APCOA fallback = %s", station_name, f"£{cost:.2f}" if cost is not None else "None")
    return cost


def _compute_bus_daily_cost(zone_fares: dict, meta: dict | None = None) -> float:
    """Compute daily round-trip bus cost from zone fare products.

    Returns the cheapest of adult_single × 2 (capped), adult_return,
    or adult_day.  Falls back to adult_return then adult_day when
    adult_single is missing.
    """
    adult_single = zone_fares.get("adult_single")
    adult_return = zone_fares.get("adult_return")
    adult_day = zone_fares.get("adult_day")

    national_cap = (meta or {}).get("national_max_single_gbp") if meta else None

    if adult_single is not None:
        if national_cap is not None:
            adult_single = min(adult_single, national_cap)
        daily = adult_single * 2
    elif adult_return is not None:
        daily = adult_return
    elif adult_day is not None:
        daily = adult_day
    else:
        return 0.0

    if adult_return is not None and adult_return < daily:
        daily = adult_return
    if adult_day is not None and adult_day < daily:
        daily = adult_day

    return round(daily, 2)


def _lookup_bus_roundtrip_cost(
    dep_stop_name: str,
    arr_stop_name: str,
    dep_point: dict | None = None,
    arr_point: dict | None = None,
) -> float | None:
    """Look up daily bus fare from data file by stop name → zone → zone pair.

    When name lookup fails, falls back to coordinate-based matching using
    the TfL stop lat/lon (available from the journey response). Within 50m
    of a known BODS stop the correct zone is returned regardless of name.

    Returns daily round-trip cost in GBP, or None if lookup fails.
    """
    fares_data = _load_bus_fares()
    if not fares_data:
        return None

    meta = fares_data.get("_meta")
    dep_norm = dep_stop_name.strip().lower()
    arr_norm = arr_stop_name.strip().lower()

    dep_alt = dep_norm.split(", ", 1)[-1] if ", " in dep_norm else dep_norm
    arr_alt = arr_norm.split(", ", 1)[-1] if ", " in arr_norm else arr_norm

    for op_key, op_data in fares_data.items():
        if op_key == "_meta":
            continue
        stop_zones = op_data.get("stop_zones", {})
        dep_zone = stop_zones.get(dep_norm) or stop_zones.get(dep_alt)
        arr_zone = stop_zones.get(arr_norm) or stop_zones.get(arr_alt)
        if dep_zone and arr_zone:
            zone_pair = f"{dep_zone}:{arr_zone}"
            zone_fares = op_data.get("zone_fares", {}).get(zone_pair)
            if zone_fares:
                return _compute_bus_daily_cost(zone_fares, meta)

    # Token-set fuzzy fallback: normalise punctuation, strip area prefix, then compare token sets
    if dep_zone is None or arr_zone is None:
        def _norm(s: str) -> set[str]:
            core = s.split(", ", 1)[-1]
            no_punct = re.sub(r"[.,;:'\"!?()]", "", core)
            return set(no_punct.split())
        dep_tokens = _norm(dep_norm)
        arr_tokens = _norm(arr_norm)
        for op_key, op_data in fares_data.items():
            if op_key == "_meta":
                continue
            stop_zones = op_data.get("stop_zones", {})
            if dep_zone is None:
                for bods_name in stop_zones:
                    bods_tokens = _norm(bods_name)
                    inter = dep_tokens & bods_tokens
                    union = dep_tokens | bods_tokens
                    if union and len(inter) / len(union) >= 0.85:
                        dep_zone = stop_zones[bods_name]
                        logger.warning("Bus fare fuzzy match dep='%s' -> '%s' zone=%s", dep_norm, bods_name, dep_zone)
                        if arr_zone is not None:
                            break
            if arr_zone is None:
                for bods_name in stop_zones:
                    bods_tokens = _norm(bods_name)
                    inter = arr_tokens & bods_tokens
                    union = arr_tokens | bods_tokens
                    if union and len(inter) / len(union) >= 0.85:
                        arr_zone = stop_zones[bods_name]
                        logger.warning("Bus fare fuzzy match arr='%s' -> '%s' zone=%s", arr_norm, bods_name, arr_zone)
                        if dep_zone is not None:
                            break
            if dep_zone and arr_zone:
                zone_pair = f"{dep_zone}:{arr_zone}"
                zone_fares = op_data.get("zone_fares", {}).get(zone_pair)
                if zone_fares:
                    return _compute_bus_daily_cost(zone_fares, meta)

    # Name lookup failed — try coordinate fallback against stop_coords index
    dep_coords = (dep_point.get("lat"), dep_point.get("lon")) if dep_point else (None, None)
    arr_coords = (arr_point.get("lat"), arr_point.get("lon")) if arr_point else (None, None)
    if dep_coords[0] is not None and arr_coords[0] is not None:
        for op_key, op_data in fares_data.items():
            if op_key == "_meta":
                continue
            stop_coords = op_data.get("stop_coords", [])
            if not stop_coords:
                continue
            dep_zone = _nearest_bus_zone(dep_coords[0], dep_coords[1], stop_coords, radius_km=0.05)
            arr_zone = _nearest_bus_zone(arr_coords[0], arr_coords[1], stop_coords, radius_km=0.05)
            if dep_zone and arr_zone:
                zone_pair = f"{dep_zone}:{arr_zone}"
                zone_fares = op_data.get("zone_fares", {}).get(zone_pair)
                if zone_fares:
                    logger.info(
                        "Bus fare by coords: dep=%s arr=%s = %s",
                        dep_stop_name,
                        arr_stop_name,
                        zone_pair,
                    )
                    return _compute_bus_daily_cost(zone_fares, meta)

    logger.warning(
        "Bus fare zone pair not found for '%s' (lat=%s) → '%s' (lat=%s)",
        dep_stop_name,
        f"{dep_coords[0]:.4f}" if dep_coords[0] else "?",
        arr_stop_name,
        f"{arr_coords[0]:.4f}" if arr_coords[0] else "?",
    )
    return None


def _nearest_bus_zone(
    lat: float,
    lon: float,
    stop_coords: list[dict],
    radius_km: float = 0.05,
) -> str | None:
    """Find the zone of the nearest BODS stop within ``radius_km`` of (lat, lon)."""
    best_dist = float("inf")
    best_zone = None
    for sc in stop_coords:
        d = _haversine_km(lat, lon, sc["lat"], sc["lon"])
        if d < best_dist:
            best_dist = d
            best_zone = sc.get("zone")
    if best_dist <= radius_km:
        return best_zone
    return None


def _nearby_bus_zones(
    lat: float,
    lon: float,
    stop_coords: list[dict],
    radius_km: float = 0.2,
) -> list[str]:
    """All distinct zone IDs within ``radius_km`` of (lat, lon), ordered by closest stop first."""
    seen: set[str] = set()
    result: list[str] = []
    for sc in sorted(stop_coords, key=lambda c: _haversine_km(lat, lon, c["lat"], c["lon"])):
        d = _haversine_km(lat, lon, sc["lat"], sc["lon"])
        if d > radius_km:
            break
        z = sc.get("zone", "")
        if z and z not in seen:
            seen.add(z)
            result.append(z)
    return result


def _lookup_station_coords(station_name: str) -> tuple[float, float] | None:
    """Find station coordinates from stations.csv by matching name (suffix-stripped)."""
    if not _STATIONS_CSV.is_file():
        return None
    clean = _shorten_station(station_name).strip().lower()
    if not clean:
        return None
    with _STATIONS_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            if row.get("stationName", "").strip().lower() == clean:
                try:
                    return float(row["lat"]), float(row["long"])
                except (ValueError, KeyError):
                    return None
    return None


def _format_route_summary(journey: dict) -> str:
    """Build a human-readable route summary from a TfL journey dict.

    Each transit leg shows mode, destination, and duration::

        walk 6m → bus(7) to Maidenhead (9m) → walk 5m
        → Train to Paddington (18m) → Bakerloo line to Oxford Circus (8m)
        → walk 7m
    """
    legs = journey.get("legs", [])
    parts: list[str] = []

    for i, leg in enumerate(legs):
        mode = leg.get("mode", {}).get("name", "?")
        duration = leg.get("duration", 0)
        instr = leg.get("instruction", {}).get("summary", "")
        arr = leg.get("arrivalPoint", {}).get("commonName", "")
        is_last = i == len(legs) - 1

        if mode == "walking":
            if is_last:
                parts.append(f"walk {duration}m")
            else:
                clean_arr = _shorten_station(arr) if arr else ""
                is_station = bool(arr) and any(arr.endswith(s) for s in _STATION_SUFFIXES if s)
                if is_station and clean_arr:
                    parts.append(f"walk to {clean_arr} ({duration}m)")
                else:
                    parts.append(f"walk {duration}m")
            continue

        clean_arr = _shorten_station(arr) if arr else ""

        if mode == "tube":
            line_from_instr = instr.split(" to ")[0] if " to " in instr else ""
            tube_line = line_from_instr.replace(" line", "").replace(" Line", "").strip()
            label = f"{tube_line} line to {clean_arr} ({duration}m)"
        elif mode == "driving":
            label = f"Drive to {clean_arr} ({duration}m)" if clean_arr else f"Drive {duration}m"
        elif mode == "bus":
            bus_num = instr.split(" bus")[0] if " bus" in instr else ""
            label = f"bus({bus_num}) to {clean_arr} ({duration}m)" if bus_num else f"Bus to {clean_arr} ({duration}m)"
        elif mode == "national-rail":
            label = f"Train to {clean_arr} ({duration}m)"
        elif mode == "overground":
            label = f"Overground to {clean_arr} ({duration}m)"
        elif mode == "dlr":
            label = f"DLR to {clean_arr} ({duration}m)"
        elif mode == "tram":
            label = f"Tram to {clean_arr} ({duration}m)"
        else:
            label = f"{mode} to {clean_arr} ({duration}m)" if clean_arr else f"{mode} {duration}m"

        parts.append(label)

    return " → ".join(parts)


def _pick_best_journey(data: dict | None) -> tuple[int | None, float | None, str]:
    """Return (duration_minutes, daily_cost_gbp, route_summary) for the shortest journey."""
    if data is None:
        return None, None, ""
    journeys = data.get("journeys", [])
    if not journeys:
        logger.debug("_pick_best_journey: no journeys in response")
        return None, None, ""
    best = min(journeys, key=lambda j: j.get("duration", 9999))
    duration = best.get("duration")
    first_leg = (best.get("legs") or [{}])[0]
    logger.debug(
        "_pick_best_journey: %d journeys, best=%dm, first_leg=%s '%s'",
        len(journeys),
        duration,
        first_leg.get("mode", {}).get("name", "?"),
        first_leg.get("arrivalPoint", {}).get("commonName", ""),
    )
    fare = best.get("fare")
    cost = None
    if fare and fare.get("totalCost") is not None:
        cost = round(fare["totalCost"] / 100.0 * 2, 2)
    route_summary = _format_route_summary(best)
    return duration, cost, route_summary


def _tfl_auth_params() -> dict[str, str]:
    params = {}
    if settings.tfl_api_key:
        params["app_key"] = settings.tfl_api_key
    return params


async def _get_drive_minutes(origin_postcode: str, station_name: str) -> int | None:
    """Driving duration (minutes) from postcode to station via ORS Directions.

    Looks up station coordinates from ``data/stations.csv`` first (by name),
    falling back to geocoding the station name if not found.
    """
    origin_coords = await _geocode(origin_postcode)
    if origin_coords is None:
        origin_coords = await _geocode_address(origin_postcode)
    if origin_coords is None:
        return None

    dest_coords = _lookup_station_coords(station_name)
    if dest_coords is None:
        dest_coords = await _geocode_address(station_name)
    if dest_coords is None:
        return None

    body = {
        "coordinates": [[origin_coords[1], origin_coords[0]], [dest_coords[1], dest_coords[0]]],
        "units": "km",
    }
    try:
        async with cached_async_client(timeout=15.0) as client:
            cached = get_cached("POST", ORS_DIRECTIONS_URL, None, json.dumps(body, sort_keys=True))
            if cached is not None:
                return round(cached["routes"][0]["summary"]["duration"] / 60)
            resp = await retry_async(
                lambda: client.post(
                    ORS_DIRECTIONS_URL,
                    headers={"Authorization": settings.ors_api_key, "Content-Type": "application/json"},
                    json=body,
                ),
                max_retries=2,
                base_delay=1.0,
                exceptions=(httpx.HTTPStatusError, httpx.RequestError),
            )
            resp.raise_for_status()
            data = resp.json()
            set_cached("POST", ORS_DIRECTIONS_URL, None, json.dumps(body, sort_keys=True), data)
            return round(data["routes"][0]["summary"]["duration"] / 60)
    except Exception:
        logger.warning("Park-and-ride ORS lookup failed for %s → %s", origin_postcode, station_name)
        return None


async def _apply_park_and_ride_to_journeys(
    data: dict,
    origin_postcode: str,
    max_walk_minutes: int,
) -> dict:
    """Replace first-leg walks exceeding ``max_walk_minutes`` with driving."""
    journeys = data.get("journeys", [])
    if not journeys:
        return data
    for journey in journeys:
        legs = journey.get("legs", [])
        if not legs:
            continue
        first = legs[0]
        if first.get("mode", {}).get("name") != "walking":
            continue
        walk_duration = first.get("duration", 0)
        logger.debug(
            "park_and_ride: walk leg=%dm to station='%s' threshold=%dm",
            walk_duration,
            first.get("arrivalPoint", {}).get("commonName", "?"),
            max_walk_minutes,
        )
        if walk_duration <= max_walk_minutes:
            logger.debug("park_and_ride: walk %dm <= %dm threshold — keeping walk", walk_duration, max_walk_minutes)
            continue
        station_name = first.get("arrivalPoint", {}).get("commonName", "")
        if not station_name:
            logger.debug("park_and_ride: walk leg has no arrivalPoint — skipping")
            continue
        drive_minutes = await _get_drive_minutes(origin_postcode, station_name)
        if drive_minutes is None:
            logger.debug(
                "park_and_ride: ORS returned None for '%s' -> '%s' — keeping walk",
                origin_postcode,
                station_name,
            )
            continue
        logger.debug(
            "park_and_ride: replacing walk %dm with drive %dm to '%s'",
            walk_duration,
            drive_minutes,
            station_name,
        )
        legs[0] = {
            "mode": {"name": "driving"},
            "duration": drive_minutes,
            "instruction": {"summary": f"Drive to {station_name}"},
            "arrivalPoint": first.get("arrivalPoint"),
        }
        old_duration = journey.get("duration", 0)
        journey["duration"] = old_duration - walk_duration + drive_minutes
    return data


async def compute_transit(
    origin_postcode: str,
    destination_postcode: str,
    label: str,
    park_and_ride: bool = False,
    allow_bus: bool = False,
) -> TransitInfo:
    """Return transit commute time using TfL Unified API (free, London focus).

    Checks the disk cache first — returns cached results even without an
    API key. Only makes a live API call when no cache exists AND a key is
    configured.

    When ``park_and_ride`` is True, any first-leg walk to the station
    longer than ``settings.max_walk_to_station_minutes`` is replaced with
    a driving leg via ORS Directions API.

    When ``allow_bus`` is True, includes "bus" in the TfL mode params and
    attempts bus fare lookup if TfL doesn't price the bus leg.
    """
    modes = ["tube", "overground", "dlr", "tram", "national-rail", "walking"]
    if allow_bus:
        modes.append("bus")
    logger.debug(
        "compute_transit: %s origin='%s' dest='%s' park_and_ride=%s allow_bus=%s modes=%s",
        label,
        origin_postcode,
        destination_postcode,
        park_and_ride,
        allow_bus,
        ",".join(modes),
    )
    url = f"{TFL_JOURNEY_URL}/{origin_postcode}/to/{destination_postcode}"
    params = {
        "nationalSearch": "true",
        "timeIs": "arriving",
        "journeyPreference": "leasttime",
        "mode": ",".join(modes),
        **_next_weekday_date_params(),
        **_tfl_auth_params(),
    }

    duration_minutes = None
    daily_cost_gbp = None
    route_summary = ""
    parking_cost_gbp = None
    bus_cost_gbp = None
    data = None

    # Check cache first
    cached = get_cached("GET", url, params)
    if cached is not None:
        data = cached
        logger.debug("compute_transit: CACHE HIT for %s (params: %s)", label, params)
    else:
        logger.debug("compute_transit: CACHE MISS for %s — making API call", label)
        try:
            async with cached_async_client(timeout=20.0) as client:
                resp = await retry_async(
                    lambda: client.get(url, params=params),
                    max_retries=2,
                    base_delay=1.0,
                    exceptions=(httpx.HTTPStatusError, httpx.RequestError),
                )
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", url, params, None, data)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("TfL could not route %s: route may be outside London area", label)
            elif e.response.status_code == 300:
                pc_match = re.search(r"[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?", origin_postcode)
                pc = pc_match.group(0).strip().upper() if pc_match else None
                # Try the full address string first (better geocoding results),
                # then fall back to postcode/outcode center
                coords = await _geocode_address(origin_postcode)
                if coords is None and pc:
                    coords = await _geocode(pc)
                if coords:
                    latlng = f"{coords[0]},{coords[1]}"
                    url2 = f"{TFL_JOURNEY_URL}/{latlng}/to/{destination_postcode}"
                    try:
                        async with cached_async_client(timeout=20.0) as c2:
                            r2 = await c2.get(url2, params=params)
                            r2.raise_for_status()
                            d2 = r2.json()
                            set_cached("GET", url2, params, None, d2)
                            duration_minutes, daily_cost_gbp, route_summary = _pick_best_journey(d2)
                    except Exception:
                        logger.warning("TfL geocode fallback failed for %s", label)
            else:
                logger.error("TfL API HTTP error for %s: %s", label, e)
        except httpx.RequestError as e:
            logger.error("TfL API request failed for %s: %s", label, e)
        except (KeyError, IndexError, TypeError) as e:
            logger.error("TfL API unexpected response for %s: %s", label, e)

    if data is not None and park_and_ride:
        data = await _apply_park_and_ride_to_journeys(
            data,
            origin_postcode,
            settings.max_walk_to_station_minutes,
        )

    if data is not None:
        duration_minutes, daily_cost_gbp, route_summary = _pick_best_journey(data)

    # Bus fare: when allow_bus and best journey has bus legs
    if allow_bus and duration_minutes is not None and data is not None:
        journeys = data.get("journeys", [])
        if journeys:
            best = min(journeys, key=lambda j: j.get("duration", 9999))
            bus_legs = [leg for leg in best.get("legs", []) if leg.get("mode", {}).get("name") == "bus"]
            if bus_legs:
                fare = best.get("fare", {})
                tfl_total_pence = fare.get("totalCost") if fare else None
                if tfl_total_pence and tfl_total_pence > 0:
                    # TfL already priced the bus — use totalCost directly
                    daily_cost_gbp = round(tfl_total_pence / 100 * 2, 2)
                else:
                    # TfL didn't price the bus — look up from data file
                    tfl_non_bus_fare = 0
                    fare_fares = fare.get("fares", []) if fare else []
                    for f in fare_fares:
                        if f.get("mode") != "bus" and f.get("cost"):
                            tfl_non_bus_fare += f["cost"]

                    total_bus_cost = 0.0
                    for bus_leg in bus_legs:
                        dep = bus_leg.get("departurePoint", {}).get("commonName", "")
                        arr = bus_leg.get("arrivalPoint", {}).get("commonName", "")
                        leg_cost = _lookup_bus_roundtrip_cost(
                            dep,
                            arr,
                            dep_point=bus_leg.get("departurePoint", {}),
                            arr_point=bus_leg.get("arrivalPoint", {}),
                        )
                        if leg_cost is not None:
                            total_bus_cost += leg_cost

                    if total_bus_cost > 0:
                        bus_cost_gbp = total_bus_cost
                        daily_cost_gbp = round(tfl_non_bus_fare / 100 * 2 + total_bus_cost, 2)

    # Parking cost: when park_and_ride and best journey has a driving leg
    if park_and_ride and duration_minutes is not None and data is not None:
        journeys = data.get("journeys", [])
        if journeys:
            best = min(journeys, key=lambda j: j.get("duration", 9999))
            legs = best.get("legs", [])
            if legs and legs[0].get("mode", {}).get("name") == "driving":
                station_name = legs[0].get("arrivalPoint", {}).get("commonName", "")
                logger.debug(
                    "parking: driving leg found, arrivalPoint='%s'",
                    station_name,
                )
                if station_name:
                    parking_cost = await _lookup_parking_cost(station_name)
                    if parking_cost is not None:
                        parking_cost_gbp = parking_cost
                        if daily_cost_gbp is not None:
                            daily_cost_gbp = round(daily_cost_gbp + parking_cost, 2)
                    else:
                        logger.debug("parking: _lookup_parking_cost returned None for '%s'", station_name)
            else:
                mode = legs[0].get("mode", {}).get("name") if legs else "no legs"
                logger.debug("parking: no driving leg (first leg mode=%s) — no parking cost", mode)

    return TransitInfo(
        destination_label=label,
        destination_postcode=destination_postcode,
        duration_minutes=duration_minutes,
        daily_cost_gbp=daily_cost_gbp,
        route_summary=route_summary,
        mode="transit",
        parking_cost_gbp=parking_cost_gbp,
        bus_cost_gbp=bus_cost_gbp,
    )


async def compute_simon_commute(property_postcode: str) -> TransitInfo:
    return await compute_transit(
        property_postcode,
        settings.simon_postcode,
        "Simon — Pimlico / Victoria",
        park_and_ride=True,
        allow_bus=False,
    )


def _pick_best_lorena_route(no_bus: TransitInfo, with_bus: TransitInfo) -> TransitInfo:
    """Compare no-bus and with-bus commute results.

    Uses the with-bus result only if it saves at least 15 minutes
    over the no-bus first-leg walk.
    """
    if with_bus.duration_minutes is None:
        return no_bus
    if no_bus.duration_minutes is None:
        return with_bus

    no_bus_saves = no_bus.duration_minutes - with_bus.duration_minutes
    if no_bus_saves >= settings.bus_walk_penalty_minutes:
        return with_bus
    return no_bus


async def _compute_google_transit(origin: str, destination: str) -> TransitInfo | None:
    """Fallback transit routing via Google Maps Routes API.

    Used when TfL doesn't find a bus leg (out-of-London areas). Returns a
    TransitInfo with bus fare looked up from BODS data, or None if Google
    also can't route the journey.
    """
    google_key = settings.google_maps_api_key
    if not google_key:
        return None

    body = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": "TRANSIT",
        "transitPreferences": {"routingPreference": "less_walking"},
        "computeAlternativeRoutes": False,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": google_key,
        "X-Goog-FieldMask": "routes.duration,routes.legs",
    }

    cached = get_cached("POST", GOOGLE_ROUTES_URL, None, json.dumps(body, sort_keys=True))
    if cached is not None:
        data = cached
    else:
        try:
            async with cached_async_client(timeout=15.0) as client:
                resp = await retry_async(
                    lambda: client.post(GOOGLE_ROUTES_URL, json=body, headers=headers),
                    max_retries=2,
                    base_delay=1.0,
                    exceptions=(httpx.HTTPStatusError, httpx.RequestError),
                )
                resp.raise_for_status()
                data = resp.json()
                set_cached("POST", GOOGLE_ROUTES_URL, None, json.dumps(body, sort_keys=True), data)
        except Exception as e:
            logger.warning("Google Routes API failed for %s → %s: %s", origin, destination, e)
            return None

    routes = data.get("routes", [])
    if not routes:
        return None

    leg = routes[0].get("legs", [{}])[0]
    duration_sec = int(routes[0].get("duration", "0s").rstrip("s"))
    duration_min = round(duration_sec / 60)

    steps = leg.get("steps", [])
    bus_legs = [s for s in steps if s.get("travelMode") == "TRANSIT" and s.get("transitDetails", {}).get("transitLine", {}).get("vehicle", {}).get("type") == "BUS"]

    total_bus_cost = 0.0
    bus_cost_gbp = None
    for bl in bus_legs:
        transit = bl.get("transitDetails", {})
        dep_stop = transit.get("stopDetails", {}).get("departureStop", {})
        arr_stop = transit.get("stopDetails", {}).get("arrivalStop", {})
        dep_name = dep_stop.get("name", "")
        arr_name = arr_stop.get("name", "")
        dep_coords = dep_stop.get("location", {}).get("latLng", {})
        arr_coords = arr_stop.get("location", {}).get("latLng", {})
        dep_point = {"lat": dep_coords.get("latitude"), "lon": dep_coords.get("longitude")} if dep_coords else None
        arr_point = {"lat": arr_coords.get("latitude"), "lon": arr_coords.get("longitude")} if arr_coords else None
        leg_cost = _lookup_bus_roundtrip_cost(dep_name, arr_name, dep_point=dep_point, arr_point=arr_point)

        if leg_cost is None and dep_point and arr_point:
            data_all = _load_bus_fares()
            for op_key, op_data in data_all.items():
                if op_key == "_meta":
                    continue
                stop_zones = op_data.get("stop_zones", {})
                stop_coords = op_data.get("stop_coords", [])
                zone_fares = op_data.get("zone_fares", {})
                dep_name_norm = re.sub(r"[.,;:'\"!?()]", "", dep_name.lower()).split(", ")[-1]
                arr_name_norm = re.sub(r"[.,;:'\"!?()]", "", arr_name.lower()).split(", ")[-1]
                known_dep = stop_zones.get(dep_name_norm)
                known_arr = stop_zones.get(arr_name_norm)
                for radius in (0.2, 0.5, 1.0, 2.0):
                    dep_zones: list[str] = [known_dep] if known_dep else _nearby_bus_zones(dep_point["lat"], dep_point["lon"], stop_coords, radius_km=radius)
                    arr_zones: list[str] = [known_arr] if known_arr else _nearby_bus_zones(arr_point["lat"], arr_point["lon"], stop_coords, radius_km=radius)
                    for dep_zone in dep_zones:
                        for arr_zone in arr_zones:
                            zk = f"{dep_zone}:{arr_zone}"
                            fares = zone_fares.get(zk) or zone_fares.get(f"{arr_zone}:{dep_zone}")
                            if fares:
                                leg_cost = _compute_bus_daily_cost(fares, data_all.get("_meta"))
                                logger.info("Google bus fare (radius=%dm): %s -> %s = %s",
                                            int(radius * 1000), dep_zone, arr_zone, leg_cost)
                                break
                        if leg_cost is not None:
                            break
                    if leg_cost is not None:
                        break

        if leg_cost is not None:
            total_bus_cost += leg_cost

    if total_bus_cost > 0:
        bus_cost_gbp = total_bus_cost
        daily_cost_gbp = round(total_bus_cost, 2)
    else:
        daily_cost_gbp = None

    steps_summary = []
    for s in steps:
        mode = s.get("travelMode", "WALK")
        instr = s.get("navigationInstruction", {}).get("instructions", "")
        steps_summary.append(f"{mode.lower()} {instr[:40]}")
    route_summary = " → ".join(steps_summary)

    return TransitInfo(
        destination_label="Lorena — Aldgate / City of London (Google)",
        destination_postcode=destination,
        duration_minutes=duration_min,
        daily_cost_gbp=daily_cost_gbp,
        route_summary=route_summary,
        mode="transit",
        bus_cost_gbp=bus_cost_gbp,
    )


async def compute_lorena_commute(property_postcode: str) -> TransitInfo:
    no_bus = await compute_transit(
        property_postcode,
        settings.lorena_postcode,
        "Lorena — Aldgate / City of London",
    )
    with_bus = await compute_transit(
        property_postcode,
        settings.lorena_postcode,
        "Lorena — Aldgate / City of London",
        allow_bus=True,
    )
    result = _pick_best_lorena_route(no_bus, with_bus)

    # Google fallback: TfL picked no-bus because it has no bus data for this area.
    # Extract Google's first-leg bus and overlay it onto TfL's route rather than
    # comparing whole routes (Google's London routing may be slower than TfL's).
    if result is no_bus and no_bus.duration_minutes is not None:
        m = re.search(r"walk.*?\((\d+)m\)", no_bus.route_summary[:60])
        walk_to_station = int(m.group(1)) if m else 0
        if walk_to_station >= settings.bus_walk_penalty_minutes:
            google_route = await _compute_google_transit(property_postcode, settings.lorena_postcode)
            if google_route and google_route.bus_cost_gbp is not None:
                bus_time = min(15, walk_to_station - settings.bus_walk_penalty_minutes)
                savings = walk_to_station - bus_time
                if savings >= settings.bus_walk_penalty_minutes:
                    new_duration = no_bus.duration_minutes - walk_to_station + bus_time
                    new_cost = no_bus.daily_cost_gbp
                    if new_cost is not None:
                        new_cost = round(new_cost + google_route.bus_cost_gbp, 2)
                    else:
                        new_cost = google_route.bus_cost_gbp
                    logger.info(
                        "Google bus overlay for %s: estimates bus %dm saves %dm over walk %dm, total=£%s",
                        property_postcode, bus_time, savings, walk_to_station, new_cost,
                    )
                    after_walk = no_bus.route_summary
                    walk_m = re.search(r"walk.*?\(\d+m\).*?\u2192\s*", after_walk)
                    if walk_m:
                        after_walk = after_walk[walk_m.end():]
                    route_summary = f"walk to bus stop (~{max(3, bus_time - 5)}m) \u2192 bus to station ({bus_time}m) \u2192 {after_walk}"
                    result = TransitInfo(
                        destination_label="Lorena \u2014 Aldgate / City of London",
                        destination_postcode=settings.lorena_postcode,
                        duration_minutes=new_duration,
                        daily_cost_gbp=new_cost,
                        route_summary=route_summary,
                        mode="transit",
                        bus_cost_gbp=google_route.bus_cost_gbp,
                    )

    return result


async def compute_commute_breakdown(
    simon_transit: TransitInfo,
    lorena_transit: TransitInfo,
    petrol: PetrolCost,
) -> CommuteBreakdown:
    simon_daily = simon_transit.daily_cost_gbp
    lorena_daily = lorena_transit.daily_cost_gbp
    bracknell_daily = petrol.cost_gbp

    yearly_total = None
    formula = ""

    if simon_daily is not None and lorena_daily is not None and bracknell_daily is not None:
        yearly_total = settings.working_weeks_per_year * (
            bracknell_daily + simon_daily + lorena_daily * settings.weekly_lorena_trips
        )
        yearly_total = round(yearly_total, 2)
        formula = (
            f"{settings.working_weeks_per_year}wk x "
            f"({settings.weekly_bracknell_trips}xBracknell_daily + "
            f"{settings.weekly_lorena_trips}xLorena_daily + "
            f"{settings.weekly_simon_trips}xSimon_daily)"
        )

    return CommuteBreakdown(
        simon_daily_gbp=simon_daily,
        lorena_daily_gbp=lorena_daily,
        bracknell_daily_gbp=bracknell_daily,
        yearly_total_gbp=yearly_total,
        formula_explanation=formula,
    )


# ---------------------------------------------------------------------------
# Petrol — OpenRouteService driving distance
# ---------------------------------------------------------------------------


def _compute_petrol_from_distance_km(round_trip_km: float) -> float:
    litres_per_100km = 235.214 / settings.petrol_mpg
    litres_used = (round_trip_km / 100) * litres_per_100km
    return round(litres_used * settings.petrol_price_per_litre, 2)


async def compute_petrol_cost(origin_postcode: str) -> PetrolCost:
    try:
        # Use postcodes.io first (more reliable for UK), fall back to ORS
        origin_coords = None
        coords = await _geocode(origin_postcode)
        if coords is None:
            coords = await _geocode_address(origin_postcode)
        if coords is not None:
            # _geocode returns (lat, lng), ORS needs [lng, lat]
            origin_coords = [coords[1], coords[0]]

        if origin_coords is None:
            logger.warning("Could not geocode origin: %s", origin_postcode)
            return PetrolCost()

        # Geocode Bracknell via postcodes.io (more reliable), fall back to ORS
        bracknell_coords = await _geocode(settings.bracknell_postcode)
        if bracknell_coords is None:
            logger.warning("Could not geocode Bracknell")
            return PetrolCost()
        # _geocode returns (lat, lng), ORS needs [lng, lat]
        dest_coords = [bracknell_coords[1], bracknell_coords[0]]

        async with cached_async_client(timeout=15.0) as client:
            body = {"coordinates": [origin_coords, dest_coords], "units": "km"}
            cached = get_cached("POST", ORS_DIRECTIONS_URL, None, json.dumps(body, sort_keys=True))
            if cached is not None:
                dir_data = cached
            else:
                dir_resp = await retry_async(
                    lambda: client.post(
                        ORS_DIRECTIONS_URL,
                        headers={
                            "Authorization": settings.ors_api_key,
                            "Content-Type": "application/json",
                        },
                        json=body,
                    ),
                    max_retries=2,
                    base_delay=1.0,
                    exceptions=(httpx.HTTPStatusError, httpx.RequestError),
                )
                dir_resp.raise_for_status()
                dir_data = dir_resp.json()
                set_cached("POST", ORS_DIRECTIONS_URL, None, json.dumps(body, sort_keys=True), dir_data)

        one_way_km = dir_data["routes"][0]["summary"]["distance"]
        one_way_duration_sec = dir_data["routes"][0]["summary"]["duration"]
        round_trip_km = round(one_way_km * 2, 1)
        round_trip_minutes = round(one_way_duration_sec * 2 / 60)
        cost = _compute_petrol_from_distance_km(round_trip_km)

        return PetrolCost(round_trip_km=round_trip_km, round_trip_minutes=round_trip_minutes, cost_gbp=cost)

    except (httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as e:
        logger.error("Petrol cost failed for %s: %s", origin_postcode, e)
        return PetrolCost()


# ---------------------------------------------------------------------------
# Schools — GIAS CSV + postcodes.io geocoding
# ---------------------------------------------------------------------------

# GIAS column name mappings — the CSVs use "FieldName (name)" format
COL_NAME = "EstablishmentName"
COL_PHASE = "PhaseOfEducation (name)"
COL_GENDER = "Gender (name)"
COL_TYPE = "TypeOfEstablishment (name)"
COL_POSTCODE = "Postcode"
COL_URN = "URN"
COL_WEBSITE = "SchoolWebsite"
COL_OFSTED = "OfstedRating (name)"
COL_INSPECTION_YEAR = "InspectionYear"

# The enriched CSV — has Latitude/Longitude columns added via scripts/enrich_schools.py
# Falls back to postcodes.io on-the-fly for any schools missing coordinates.
SCHOOLS_CSV_PATH = Path("data/edubaseall_enriched.csv")

FEE_PAYING_TYPES = frozenset(
    {
        "independent school",
        "other independent school",
        "independent special school",
        "non-maintained special school",
    }
)

# In-memory cache: postcode -> (lat, lng)
_geo_cache: dict[str, tuple[float, float]] = {}
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


async def _geocode_nominatim(query: str) -> tuple[float, float] | None:
    """Geocode a place name via Nominatim (free, 1 req/sec max).

    Respects Nominatim's usage policy by enforcing at least 1 second
    between consecutive calls. If still rate-limited, sets exhaustion
    flag and returns None.
    """
    if _api_state.nominatim_exhausted:
        return None
    cache_key = f"nom::{query.strip().upper()}"
    if cache_key in _geo_cache:
        return _geo_cache[cache_key]
    # Strip trailing postcode so Nominatim doesn't choke on ", SL6" etc.
    clean = _END_PC_RE.sub("", query).strip()
    # Enforce 1 req/sec rate limit
    now = asyncio.get_event_loop().time()
    since_last = now - _api_state.nominatim_last_call
    if since_last < 1.0:
        await asyncio.sleep(1.0 - since_last)
    params = {"q": f"{clean}, UK", "format": "json", "limit": 1}
    cached = get_cached("GET", NOMINATIM_URL, params, None)
    if cached is not None:
        data = cached
        if data:
            lat = float(data[0]["lat"])
            lng = float(data[0]["lon"])
            _geo_cache[cache_key] = (lat, lng)
            return (lat, lng)
    else:
        try:
            async with cached_async_client(timeout=10.0) as client:
                resp = await client.get(
                    NOMINATIM_URL,
                    params=params,
                    headers={"User-Agent": "HousesApp/1.0"},
                )
                _api_state.nominatim_last_call = asyncio.get_event_loop().time()
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", NOMINATIM_URL, params, None, data)
                if data:
                    lat = float(data[0]["lat"])
                    lng = float(data[0]["lon"])
                    _geo_cache[cache_key] = (lat, lng)
                    return (lat, lng)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                _api_state.nominatim_exhausted = True
            logger.warning("Nominatim geocoding failed for %s (%s)", query, exc.response.status_code)
        except Exception:
            logger.warning("Nominatim geocoding failed for: %s", query)
    return None


async def _geocode_address(address: str) -> tuple[float, float] | None:
    """Geocode a free-form UK address.

    Tries Google Maps, ORS Pelias, then Nominatim as final fallback.
    Used when postcodes.io can't resolve an outcode.
    """
    cache_key = f"addr::{address.strip().upper()}"
    if cache_key in _geo_cache:
        return _geo_cache[cache_key]

    # Try Google Maps Geocoding first (best accuracy for UK streets)
    google_key = settings.google_maps_api_key
    if google_key:
        google_geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": f"{address}, UK", "key": google_key}
        cached = get_cached("GET", google_geocode_url, params, None)
        if cached is not None:
            data = cached
            if data.get("status") == "OK" and data.get("results"):
                loc = data["results"][0]["geometry"]["location"]
                lat, lng = loc["lat"], loc["lng"]
                _geo_cache[cache_key] = (lat, lng)
                return (lat, lng)
        else:
            try:
                async with cached_async_client(timeout=10.0) as client:
                    resp = await client.get(
                        google_geocode_url,
                        params=params,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    set_cached("GET", google_geocode_url, params, None, data)
                    if data.get("status") == "OK" and data.get("results"):
                        loc = data["results"][0]["geometry"]["location"]
                        lat, lng = loc["lat"], loc["lng"]
                        _geo_cache[cache_key] = (lat, lng)
                        return (lat, lng)
            except Exception:
                logger.warning("Google Maps geocoding failed for %s", address)

    # Fallback to ORS Pelias (skip if exhausted to avoid hammering)
    api_key = settings.ors_api_key
    if api_key and not _api_state.ors_geo_exhausted:
        params = {"text": f"{address}, UK", "size": 1}
        cached = get_cached("GET", ORS_GEOCODE_URL, params, None)
        if cached is not None:
            data = cached
            features = data.get("features", [])
            if features:
                lng, lat = features[0]["geometry"]["coordinates"]
                _geo_cache[cache_key] = (lat, lng)
                return (lat, lng)
        else:
            try:
                async with cached_async_client(timeout=10.0) as client:
                    resp = await retry_async(
                        lambda: client.get(
                            ORS_GEOCODE_URL,
                            params=params,
                            headers={"Authorization": api_key},
                        ),
                        max_retries=2,
                        base_delay=0.5,
                        exceptions=(httpx.HTTPStatusError, httpx.RequestError),
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    set_cached("GET", ORS_GEOCODE_URL, params, None, data)
                    features = data.get("features", [])
                    if features:
                        lng, lat = features[0]["geometry"]["coordinates"]
                        _geo_cache[cache_key] = (lat, lng)
                        return (lat, lng)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 429):
                    _api_state.ors_geo_exhausted = True
                logger.warning("ORS geocoding failed for %s (%s)", address, exc.response.status_code)
            except Exception:
                logger.warning("ORS geocoding failed for %s", address)

    # Final fallback: Nominatim (free, no key, works for UK)
    result = await _geocode_nominatim(address)
    if result:
        _geo_cache[cache_key] = result
    return result


async def _geocode(postcode: str) -> tuple[float, float] | None:
    """Geocode a UK postcode via postcodes.io with in-memory caching.

    Supports both full postcodes ("SL6 1AA") and outcodes ("SL6").
    """
    key = postcode.strip().upper()
    if not key:
        return None
    if key in _geo_cache:
        return _geo_cache[key]

    is_outcode = bool(_OUTCODE_RE.match(key))

    url = f"{OUTCODES_IO_URL}/{key}" if is_outcode else f"{POSTCODES_IO_URL}/{key}"
    cached = get_cached("GET", url, None, None)
    if cached is not None:
        data = cached
        result = data.get("result")
        if not result:
            return None
        latlng = result["latitude"], result["longitude"]
        _geo_cache[key] = latlng
        return latlng
    else:
        try:
            async with cached_async_client(timeout=10.0) as client:
                resp = await retry_async(
                    lambda: client.get(url),
                    max_retries=2,
                    base_delay=0.5,
                    exceptions=(httpx.HTTPStatusError, httpx.RequestError),
                )
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", url, None, None, data)
                result = data.get("result")
                if not result:
                    return None
                latlng = result["latitude"], result["longitude"]
                _geo_cache[key] = latlng
                return latlng
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                _geo_cache[key] = None
                set_cached("GET", url, None, None, {})  # cache empty dict for 404
            else:
                logger.warning("Geocode HTTP error for %s: %s", key, e)
            return None
        except Exception:
            logger.exception("Failed to geocode postcode: %s", key)
            return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _load_schools() -> list[dict]:
    if not SCHOOLS_CSV_PATH.is_file():
        logger.warning("Schools CSV not found at %s", SCHOOLS_CSV_PATH)
        return []
    with SCHOOLS_CSV_PATH.open(newline="", encoding="latin-1") as f:
        return list(csv.DictReader(f))


def _boys_eligible(school: dict) -> bool:
    gender = (school.get(COL_GENDER) or "").strip().lower()
    if gender not in ("mixed", "boys"):
        return False
    estab_type = (school.get(COL_TYPE) or "").strip().lower()
    return estab_type not in FEE_PAYING_TYPES


def _phase_filter(school: dict, target: str) -> bool:
    phase = (school.get(COL_PHASE) or "").strip().lower()
    return target in phase


def _school_to_info(school: dict, dist_km: float, school_type: str) -> SchoolInfo:
    walk_mins = round(dist_km / 5 * 60) if dist_km else None
    # Bus time only makes sense when walking is impractical (> 20 min)
    bus_mins = None
    return SchoolInfo(
        name=school.get(COL_NAME, "Unknown"),
        type=school_type,
        distance_km=round(dist_km, 2) if dist_km else None,
        gender=(school.get(COL_GENDER) or "").strip().lower(),
        fee_paying=False,
        walking_time_minutes=walk_mins,
        bus_time_minutes=bus_mins,
        urn=school.get(COL_URN, ""),
        website=school.get(COL_WEBSITE, ""),
        ofsted_rating=school.get(COL_OFSTED, ""),
        inspection_year=school.get(COL_INSPECTION_YEAR, ""),
    )


def _school_coords(school: dict) -> tuple[float, float] | None:
    """Read lat/lng from the enriched CSV row."""
    try:
        lat = school.get("Latitude")
        lng = school.get("Longitude")
        if lat and lng:
            return float(lat), float(lng)
    except (ValueError, TypeError):
        pass
    return None


async def _find_nearest_boys(
    postcode: str,
    target_phase: str,
    address: str = "",
) -> SchoolInfo | None:
    schools = _load_schools()
    if not schools:
        return None

    property_coords = await _geocode(postcode)
    if property_coords is None and address:
        property_coords = await _geocode_address(address)
    if property_coords is None:
        return None

    origin_lat, origin_lon = property_coords
    candidates: list[tuple[float, dict]] = []

    for school in schools:
        if not _phase_filter(school, target_phase):
            continue
        if not _boys_eligible(school):
            continue

        school_coords = _school_coords(school)
        if school_coords is None:
            school_postcode = school.get(COL_POSTCODE, "")
            if not school_postcode:
                continue
            school_coords = await _geocode(school_postcode)
            if school_coords is None:
                continue

        dist = _haversine_km(origin_lat, origin_lon, school_coords[0], school_coords[1])
        if dist <= settings.school_search_radius_km:
            candidates.append((dist, school))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    _, best = candidates[0]
    info = _school_to_info(best, candidates[0][0], target_phase)

    if (
        info.type == "secondary"
        and info.walking_time_minutes
        and info.walking_time_minutes > 20
        and settings.google_maps_api_key
    ):
        school_coords = _school_coords(best)
        if school_coords:
            routes_url = "https://routes.googleapis.com/directions/v2:computeRoutes"
            body = {
                "origin": {
                    "location": {"latLng": {"latitude": origin_lat, "longitude": origin_lon}},
                },
                "destination": {
                    "location": {"latLng": {"latitude": school_coords[0], "longitude": school_coords[1]}},
                },
                "travelMode": "TRANSIT",
            }
            cached = get_cached("POST", routes_url, None, json.dumps(body, sort_keys=True))
            if cached is not None:
                data = cached
                leg = data.get("routes", [{}])[0].get("legs", [{}])[0]
                duration_s = leg.get("duration", "")
                if duration_s and duration_s.endswith("s"):
                    with contextlib.suppress(ValueError):
                        info.bus_time_minutes = round(int(duration_s.rstrip("s")) / 60)
                steps = leg.get("steps", [])
                for s in steps:
                    td = s.get("transitDetails")
                    if td:
                        line = td.get("transitLine", {}).get("nameShort", "")
                        dep = td.get("stopDetails", {}).get("departureStop", {}).get("name", "")
                        if line and dep:
                            info.bus_route = f"{line} from {dep}"
                            break
            else:
                try:
                    async with cached_async_client(timeout=10.0) as c:
                        resp = await c.post(
                            routes_url,
                            headers={
                                "X-Goog-Api-Key": settings.google_maps_api_key,
                                "X-Goog-FieldMask": "routes.legs.duration,routes.legs.steps.transitDetails",
                                "Content-Type": "application/json",
                            },
                            json=body,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            set_cached("POST", routes_url, None, json.dumps(body, sort_keys=True), data)
                            leg = data.get("routes", [{}])[0].get("legs", [{}])[0]
                            duration_s = leg.get("duration", "")
                            if duration_s and duration_s.endswith("s"):
                                with contextlib.suppress(ValueError):
                                    info.bus_time_minutes = round(int(duration_s.rstrip("s")) / 60)
                            steps = leg.get("steps", [])
                            for s in steps:
                                td = s.get("transitDetails")
                                if td:
                                    line = td.get("transitLine", {}).get("nameShort", "")
                                    dep = td.get("stopDetails", {}).get("departureStop", {}).get("name", "")
                                    if line and dep:
                                        info.bus_route = f"{line} from {dep}"
                                        break
                        else:
                            logger.error(
                                "Routes API returned %d — enable routes.googleapis.com",
                                resp.status_code,
                            )
                except Exception as e:
                    logger.error("Bus directions failed: %s", e)

    return info


async def find_nearest_boys_primary(postcode: str, address: str = "") -> SchoolInfo | None:
    return await _find_nearest_boys(postcode, "primary", address)


async def find_nearest_boys_secondary(postcode: str, address: str = "") -> SchoolInfo | None:
    return await _find_nearest_boys(postcode, "secondary", address)
