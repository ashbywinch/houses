"""Walk time to town centre and nearby amenities."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from houses.api_cache import cached_async_client, get_cached, with_cache
from houses.config import settings
from houses.retry import retry_async

logger = logging.getLogger(__name__)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lng points."""
    import math

    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


ORS_WALKING_URL = "https://api.openrouteservice.org/v2/directions/foot-walking"
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
GOOGLE_MAPS_PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"

_POSTCODE_FULL_RE = re.compile(
    r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$",
    re.IGNORECASE,
)
_POSTCODE_OUTCODE_RE = re.compile(
    r"^[A-Z]{1,2}[0-9][A-Z0-9]?$",
    re.IGNORECASE,
)

# UK ceremonial counties that sometimes appear in address lines.
# Filtered out during town extraction so "Berkshire" doesn't win over "Maidenhead".
_KNOWN_COUNTIES = frozenset(
    {
        "berkshire",
        "buckinghamshire",
        "oxfordshire",
        "surrey",
        "kent",
        "essex",
        "hertfordshire",
        "bedfordshire",
        "cambridgeshire",
        "suffolk",
        "norfolk",
        "northamptonshire",
        "warwickshire",
        "worcestershire",
        "gloucestershire",
        "somerset",
        "devon",
        "cornwall",
        "dorset",
        "wiltshire",
        "hampshire",
        "west sussex",
        "east sussex",
        "middlesex",
        "lancashire",
        "yorkshire",
        "cheshire",
        "derbyshire",
        "nottinghamshire",
        "lincolnshire",
        "leicestershire",
        "staffordshire",
        "shropshire",
        "herefordshire",
        "durham",
        "northumberland",
        "cumbria",
        "greater manchester",
        "merseyside",
        "tyne and wear",
        "west midlands",
        "south yorkshire",
        "west yorkshire",
    }
)

_town_geo_cache: dict[str, tuple[float, float]] = {}


# Per-process-run API exhaustion tracking.
# Set when an API returns a usage-limit error so subsequent calls
# skip straight to the fallback instead of hammering the dead endpoint.
class _APIState:
    places_exhausted: bool = False
    ors_geo_exhausted: bool = False
    nominatim_exhausted: bool = False
    nominatim_last_call: float = 0.0


_api_state = _APIState()


def _extract_town(address: str) -> str:
    parts = [p.strip() for p in address.split(",")]
    filtered = [p for p in parts if p and not _POSTCODE_FULL_RE.match(p) and not _POSTCODE_OUTCODE_RE.match(p)]
    non_county = [p for p in filtered if p.lower().strip() not in _KNOWN_COUNTIES]
    return non_county[-1] if non_county else (filtered[-1] if filtered else "")


# Suffixes to strip from town names before geocoding, e.g. "Maidenhead Station Area" -> "Maidenhead"
_TOWN_SUFFIXES = re.compile(
    r"\s+(Station Area|Station|Area|Village|Town Centre|Centre|Villlage|Park|Business Park|Bottom)$",
    re.IGNORECASE,
)


async def _geocode_town(town: str) -> tuple[float, float] | None:
    key = town.strip().upper()
    if not key:
        return None
    if key in _town_geo_cache:
        return _town_geo_cache[key]

    ors_params = {"text": f"{town}, UK", "size": 1}
    ors_cached = get_cached("GET", ORS_GEOCODE_URL, ors_params)
    if ors_cached is not None:
        features = ors_cached.get("features", [])
        if features:
            lng, lat = features[0]["geometry"]["coordinates"]
            _town_geo_cache[key] = (lat, lng)
            return (lat, lng)

    # Try ORS Pelias (skip if exhausted to avoid hammering)
    if settings.ors_api_key and not _api_state.ors_geo_exhausted:
        try:
            async with cached_async_client(timeout=10.0) as client:

                async def _fetch():
                    resp = await retry_async(
                        lambda: client.get(
                            ORS_GEOCODE_URL,
                            params=ors_params,
                            headers={"Authorization": settings.ors_api_key},
                        ),
                        max_retries=2,
                        base_delay=1.0,
                        exceptions=(httpx.HTTPStatusError, httpx.RequestError),
                    )
                    resp.raise_for_status()
                    return resp.json()

                data = await with_cache("GET", ORS_GEOCODE_URL, params=ors_params, fetch=_fetch)
                features = data.get("features", [])
                if features:
                    lng, lat = features[0]["geometry"]["coordinates"]
                    _town_geo_cache[key] = (lat, lng)
                    return (lat, lng)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (403, 429):
                _api_state.ors_geo_exhausted = True
            logger.warning("ORS geocoding failed for town: %s (%s)", town, exc.response.status_code)
        except Exception:
            logger.warning("ORS geocoding failed for town: %s", town)

    # Fallback: Nominatim (free, no key)
    nom_params = {"q": f"{town}, UK", "format": "json", "limit": 1}
    nom_cached = get_cached("GET", "https://nominatim.openstreetmap.org/search", nom_params)
    if nom_cached is not None and nom_cached:
        _town_geo_cache[key] = (float(nom_cached[0]["lat"]), float(nom_cached[0]["lon"]))
        return _town_geo_cache[key]

    if not _api_state.nominatim_exhausted:
        now = asyncio.get_event_loop().time()
        since_last = now - _api_state.nominatim_last_call
        if since_last < 1.0:
            await asyncio.sleep(1.0 - since_last)
        try:
            async with cached_async_client(timeout=10.0) as client:

                async def _fetch_nom():
                    resp = await client.get(
                        "https://nominatim.openstreetmap.org/search",
                        params=nom_params,
                        headers={"User-Agent": "HousesApp/1.0"},
                    )
                    _api_state.nominatim_last_call = asyncio.get_event_loop().time()
                    resp.raise_for_status()
                    return resp.json()

                nom_url = "https://nominatim.openstreetmap.org/search"
                data = await with_cache("GET", nom_url, params=nom_params, fetch=_fetch_nom)
                if data:
                    lat = float(data[0]["lat"])
                    lng = float(data[0]["lon"])
                    _town_geo_cache[key] = (lat, lng)
                    return (lat, lng)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                _api_state.nominatim_exhausted = True
            logger.warning("Nominatim geocoding failed for town: %s (%s)", town, exc.response.status_code)
        except Exception:
            logger.warning("Nominatim geocoding failed for town: %s", town)

    # If exact town failed, try stripping suffixes like "Station Area" → "Maidenhead"
    stripped = _TOWN_SUFFIXES.sub("", town).strip()
    if stripped and stripped.upper() != key:
        result = await _geocode_town(stripped)
        if result:
            _town_geo_cache[key] = result
            return result

    return None


async def _walk_duration(
    lat: float,
    lng: float,
    town_centre: tuple[float, float],
) -> int | None:
    origin = [lng, lat]
    dest = [town_centre[1], town_centre[0]]
    body = {"coordinates": [origin, dest]}
    cached = get_cached("POST", ORS_WALKING_URL, None, json.dumps(body, sort_keys=True))
    if cached is not None:
        return round(cached["routes"][0]["summary"]["duration"] / 60)
    try:
        async with cached_async_client(timeout=15.0) as client:

            async def _fetch():
                resp = await retry_async(
                    lambda: client.post(
                        ORS_WALKING_URL,
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
                resp.raise_for_status()
                return resp.json()

            data = await with_cache("POST", ORS_WALKING_URL, body=body, fetch=_fetch)
        return round(data["routes"][0]["summary"]["duration"] / 60)
    except (httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as e:
        logger.warning("ORS walk directions failed: %s", e)
        return None


async def _nearby_amenities(lat: float, lng: float) -> str:
    if not settings.google_maps_api_key:
        return ""

    types = [
        "supermarket",
        "park",
        "pharmacy",
        "convenience_store",
    ]
    places = ""
    google_failed = False

    # Skip Places if exhausted to avoid hammering
    places_body = {
        "includedTypes": types,
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": 1000.0,
            }
        },
    }
    places_cached = get_cached("POST", GOOGLE_MAPS_PLACES_URL, None, json.dumps(places_body, sort_keys=True))
    if places_cached is not None:
        return _format_places(places_cached, lat, lng)

    if not _api_state.places_exhausted:
        try:
            async with cached_async_client(timeout=15.0) as client:

                async def _fetch_places():
                    resp = await retry_async(
                        lambda: client.post(
                            GOOGLE_MAPS_PLACES_URL,
                            headers={
                                "X-Goog-Api-Key": settings.google_maps_api_key,
                                "X-Goog-FieldMask": "places.displayName,places.types,places.location",
                                "Content-Type": "application/json",
                            },
                            json=places_body,
                        ),
                        max_retries=2,
                        base_delay=1.0,
                        exceptions=(httpx.HTTPStatusError, httpx.RequestError),
                    )
                    resp.raise_for_status()
                    return resp.json()

                data = await with_cache("POST", GOOGLE_MAPS_PLACES_URL, body=places_body, fetch=_fetch_places)
            result = _format_places(data, lat, lng)
            if result:
                return result
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                _api_state.places_exhausted = True
            logger.warning("Google Places API failed (%s), falling back to Overpass", exc.response.status_code)
            google_failed = True
        except (httpx.RequestError, KeyError, IndexError) as e:
            logger.warning("Google Places API failed (%s), falling back to Overpass", e)
            google_failed = True

    # Fallback: OpenStreetMap Overpass API (free, no key)
    if google_failed or not places:
        overpass_url = "https://overpass-api.de/api/interpreter"
        overpass_query = (
            f"[out:json][timeout:10];"
            f'(node(around:1000,{lat},{lng})["shop"~"supermarket|convenience"];'
            f'node(around:1000,{lat},{lng})["amenity"="pharmacy"];'
            f'way(around:1000,{lat},{lng})["leisure"="park"];'
            f");out center 5;"
        )
        overpass_params = {"data": overpass_query}
        overpass_cached = get_cached("GET", overpass_url, overpass_params)
        if overpass_cached is not None:
            return _format_overpass(overpass_cached, lat, lng)
        try:
            async with cached_async_client(timeout=15.0) as client:

                async def _fetch_overpass():
                    resp = await client.get(
                        overpass_url,
                        params=overpass_params,
                        headers={"Accept": "application/json", "User-Agent": "HousesApp/1.0"},
                    )
                    resp.raise_for_status()
                    return resp.json()

                data = await with_cache("GET", overpass_url, params=overpass_params, fetch=_fetch_overpass)
            places = _format_overpass(data, lat, lng)
        except Exception as e:
            logger.warning("Overpass fallback failed: %s: %s", type(e).__name__, e)

    return places


def _format_places(data: dict, lat: float, lng: float) -> str:
    """Format Google Places response into a human-readable string."""
    google_places = data.get("places", [])
    if not google_places:
        return ""
    hits = []
    for p in google_places:
        p_types = set(p.get("types", []))
        if p_types & {
            "transit_station",
            "bus_stop",
            "bus_station",
            "locality",
            "administrative_area_level_3",
            "administrative_area_level_4",
        }:
            continue
        name = p.get("displayName", {}).get("text", "Unknown")
        location = p.get("location", {})
        place_lat = location.get("latitude")
        place_lng = location.get("longitude")
        if place_lat is not None and place_lng is not None:
            dist_km = _haversine_km(lat, lng, place_lat, place_lng)
            walk_min = max(1, round(dist_km / 5 * 60))
            hits.append((walk_min, f"{name} ({walk_min}m)"))
        else:
            hits.append((999, name))
    hits.sort(key=lambda x: x[0])
    return " | ".join(name for _, name in hits[:5])


def _format_overpass(data: dict, lat: float, lng: float) -> str:
    """Format Overpass API response into a human-readable string."""
    elements = data.get("elements", [])
    hits = []
    for e in elements:
        tags = e.get("tags", {})
        name = tags.get("name", "")
        if not name:
            continue
        e_lat = e.get("lat") or (e.get("center") or {}).get("lat")
        e_lng = e.get("lon") or (e.get("center") or {}).get("lon")
        if e_lat is not None and e_lng is not None:
            dist_km = _haversine_km(lat, lng, e_lat, e_lng)
            walk_min = max(1, round(dist_km / 5 * 60))
            hits.append((walk_min, f"{name} ({walk_min}m)"))
        else:
            hits.append((999, name))
    hits.sort(key=lambda x: x[0])
    return " | ".join(name for _, name in hits[:5])


async def enrich_walkability(
    lat: float,
    lng: float,
    address: str,
) -> dict[str, Any]:
    walk_to_town_minutes: int | None = None
    town = _extract_town(address)

    if town and settings.ors_api_key:
        town_centre = await _geocode_town(town)
        if town_centre:
            walk_to_town_minutes = await _walk_duration(lat, lng, town_centre)
        else:
            logger.warning(
                "Could not geocode town centre for '%s' from address: %s",
                town,
                address,
            )
    else:
        reason = "no ORS key" if not settings.ors_api_key else "no town extracted"
        logger.warning(
            "Skipping walk time (%s) for address: %s",
            reason,
            address,
        )

    amenities = await _nearby_amenities(lat, lng)

    # Sanitize walk time: ignore impossible values (ORS can return 0 or huge numbers
    # when it can't find a proper route or geocoding is wrong)
    if walk_to_town_minutes is not None and (walk_to_town_minutes <= 0 or walk_to_town_minutes > 180):
        walk_to_town_minutes = None

    return {
        "walk_to_town_minutes": walk_to_town_minutes,
        "amenities": amenities,
    }
