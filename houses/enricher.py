"""Transit commute, petrol cost, and school lookup logic.

Uses TfL Unified API for transit routing, OpenRouteService for
driving distances, and UK government GIAS school data.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

import httpx

from houses.api_cache import cached_async_client, get_cached, set_cached
from houses.attempt import Attempt
from houses.commute import Commute, CommuteBreakdown
from houses.config import settings
from houses.location import _geocode_address, geocode
from houses.retry import retry_async
from houses.stations import Station
from houses.stations import find as find_station

logger = logging.getLogger(__name__)

TFL_JOURNEY_URL = "https://api.tfl.gov.uk/Journey/JourneyResults"
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
                clean_arr = Station.short_name(arr) if arr else ""
                is_station = bool(arr) and Station.short_name(arr) != arr
                if is_station and clean_arr:
                    parts.append(f"walk to {clean_arr} ({duration}m)")
                else:
                    parts.append(f"walk {duration}m")
            continue

        clean_arr = Station.short_name(arr) if arr else ""

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
    origin_coords = (await geocode(origin_postcode)).value_or_none()
    if origin_coords is None:
        origin_coords = (await _geocode_address(origin_postcode)).value_or_none()
    if origin_coords is None:
        return None

    station = find_station(station_name)
    dest_coords = station.location if station else None
    if dest_coords is None:
        dest_coords = (await _geocode_address(station_name)).value_or_none()
    if dest_coords is None:
        return None

    dest_lat = dest_coords.lat
    dest_lng = dest_coords.lon

    body = {
        "coordinates": [[origin_coords.lon, origin_coords.lat], [dest_lng, dest_lat]],
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


async def compute_simon_commute(property_postcode: str) -> Attempt[Commute]:
    from houses.routing import _with_label, get_commute

    result = await get_commute(property_postcode, settings.simon_postcode, has_car=True, max_walk_minutes=15)
    if result.is_succeeded:
        commute = result.value_or_none()
        return Attempt.succeeded(
            _with_label(commute, "Simon — Pimlico / Victoria", settings.simon_postcode),
            result.source,
        )
    # Propagate the failure reason from get_commute
    return Attempt.impossible(result.source, result.reason)


async def compute_lorena_commute(property_postcode: str) -> Attempt[Commute]:
    from houses.routing import _with_label, get_commute

    result = await get_commute(property_postcode, settings.lorena_postcode, has_car=False, max_walk_minutes=30)
    if result.is_succeeded:
        commute = result.value_or_none()
        return Attempt.succeeded(
            _with_label(commute, "Lorena — Aldgate / City of London", settings.lorena_postcode),
            result.source,
        )
    return Attempt.impossible(result.source, result.reason)


def _pick_best_lorena_route(no_bus: Commute, with_bus: Commute) -> Commute:
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


async def compute_commute_breakdown(
    simon_transit: Commute,
    lorena_transit: Commute,
    bracknell: Commute,
) -> CommuteBreakdown:
    simon_daily = simon_transit.daily_cost_gbp
    lorena_daily = lorena_transit.daily_cost_gbp
    bracknell_daily = bracknell.daily_cost_gbp

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


async def compute_petrol_cost(origin_postcode: str) -> Attempt[Commute]:
    """Bracknell commute — driving cost via ORS.

    Note: This still exists as a separate function because the sheet
    always shows a Bracknell cost, even when transit might be faster.
    The ``get_commute`` function (in routing.py) handles the
    transit-vs-driving comparison for other callers.
    """
    from houses.routing import _drive_commute, _with_label

    commute = await _drive_commute(origin_postcode, settings.bracknell_postcode)
    if commute:
        return Attempt.succeeded(
            _with_label(commute, "Bracknell Office (RG12 8YA)", settings.bracknell_postcode),
            "ors",
        )
    return Attempt.impossible("petrol", "could not route to Bracknell")
