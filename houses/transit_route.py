"""Park-and-ride and drive-time helpers for transit route planning."""

from __future__ import annotations

import json
import logging

from houses.api_cache import cached_async_client, get_cached, set_cached
from houses.config import settings
from houses.location import _geocode_address, geocode
from houses.stations import find as find_station

logger = logging.getLogger(__name__)

OUTCODES_IO_URL = "https://api.postcodes.io/outcodes"
POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


async def _get_drive_minutes(origin_postcode: str, station_name: str) -> int | None:
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
            resp = await client.post(
                ORS_DIRECTIONS_URL,
                headers={"Authorization": settings.ors_api_key, "Content-Type": "application/json"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            set_cached("POST", ORS_DIRECTIONS_URL, None, json.dumps(body, sort_keys=True), data)
            return round(data["routes"][0]["summary"]["duration"] / 60)
    except Exception:
        logger.warning(
            "Park-and-ride ORS lookup failed for %s \u2192 %s (url=%s)",
            origin_postcode,
            station_name,
            ORS_DIRECTIONS_URL,
        )
        return None


async def _apply_park_and_ride_to_journeys(
    data: dict,
    origin_postcode: str,
    max_walk_minutes: int,
    _drive_fn=None,
) -> dict:
    get_drive = _drive_fn if _drive_fn is not None else _get_drive_minutes
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
            logger.debug(
                "park_and_ride: walk %dm <= %dm threshold \u2014 keeping walk", walk_duration, max_walk_minutes
            )
            continue
        station_name = first.get("arrivalPoint", {}).get("commonName", "")
        if not station_name:
            logger.debug("park_and_ride: walk leg has no arrivalPoint \u2014 skipping")
            continue
        drive_minutes = await get_drive(origin_postcode, station_name)
        if drive_minutes is None:
            logger.debug(
                "park_and_ride: ORS returned None for '%s' -> '%s' \u2014 keeping walk",
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
