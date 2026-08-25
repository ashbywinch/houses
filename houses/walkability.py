"""Walk time to town centre and nearby amenities."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from houses.api_cache import cached_async_client, get_cached, set_cached, with_cache
from houses.geopoint import GeoPoint
from houses.location import PropertyLocation, WalkabilityFns
from houses.settings import settings

logger = logging.getLogger(__name__)


ORS_WALKING_URL = "https://api.openrouteservice.org/v2/directions/foot-walking"
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
GOOGLE_MAPS_PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"

_POSTCODE_FULL_RE = re.compile(
    r"[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$",
    re.IGNORECASE,
)
_POSTCODE_OUTCODE_RE = re.compile(
    r"[A-Z]{1,2}[0-9][A-Z0-9]?$",
    re.IGNORECASE,
)

# UK ceremonial counties that sometimes appear in address lines.
# Filtered out during town extraction so "Berkshire" doesn't win over "Maidenhead".
KNOWN_COUNTIES = frozenset(
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
SECONDS_PER_MINUTE = 60
HTTP_TOO_MANY_REQUESTS = 429
HTTP_5XX_START = 500
HTTP_5XX_END = 600
WALKING_SPEED_KMH = 5
MINUTES_PER_HOUR = 60
MAX_PLAUSIBLE_WALK_MINUTES = 180


def extract_town(address: str) -> str:
    parts = [p.strip() for p in address.split(",")]
    # Use search() so postcodes embedded in a segment (e.g. "Surrey. KT9 2HN") are detected.
    filtered = [p for p in parts if p and not _POSTCODE_FULL_RE.search(p) and not _POSTCODE_OUTCODE_RE.search(p)]
    non_county = [p for p in filtered if p.lower().strip() not in KNOWN_COUNTIES]
    candidate = non_county[-1] if non_county else (filtered[-1] if filtered else "")
    # Strip trailing descriptions like " - Backing the River Wye"
    if " - " in candidate:
        candidate = candidate.split(" - ")[0].strip()
    return candidate


async def _extract_town_centre(lat: float, lng: float, town: str) -> GeoPoint | None:
    """Resolve a town name to coordinates, used for walkability enrichment."""
    loc = await PropertyLocation.from_town(town)
    return loc.coordinates.value_or_none()


async def _find_town_centre_by_reverse_geocode(lat: float, lng: float) -> GeoPoint | None:
    """Use ORS Pelias reverse geocode to find the nearest town and its centre."""

    rev_url = ORS_GEOCODE_URL.replace("/search", "/reverse")
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    params = {"point.lat": lat, "point.lon": lng, "size": 1, "boundary.country": "GBR"}

    cached = get_cached("GET", rev_url, params, None)
    if cached is not None:
        data = cached
    else:
        try:
            async with cached_async_client(timeout=10.0) as client:
                resp = await client.get(rev_url, params=params)
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", rev_url, params, None, data)
        except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException):
            raise  # transient — let DAG retry handle it
        # lucidlint: ignore broad-except deliberate fallback — reverse-geocode failure returns None
        except Exception:
            logger.warning("ORS reverse geocode failed for (%.4f, %.4f)", lat, lng, exc_info=True)
            return None

    features = data.get("features", [])
    if not features:
        return None
    props = features[0].get("properties", {})
    town = props.get("locality") or props.get("borough")
    if not town:
        return None
    # Forward-geocode the town name to get its centre
    return await _extract_town_centre(lat, lng, town)


async def _walk_duration(
    lat: float,
    lng: float,
    town_centre: GeoPoint,
) -> int | None:
    origin = [lng, lat]
    dest = [town_centre.lon, town_centre.lat]
    body = {"coordinates": [origin, dest]}
    try:
        async with cached_async_client(timeout=15.0) as client:

            async def _fetch():
                resp = await client.post(
                    ORS_WALKING_URL,
                    headers={
                        "Authorization": settings.ors_api_key,
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                resp.raise_for_status()
                return resp.json()

            data = await with_cache("POST", ORS_WALKING_URL, body=body, fetch=_fetch)
        return round(data["routes"][0]["summary"]["duration"] / SECONDS_PER_MINUTE)
    except (KeyError, IndexError) as e:
        logger.warning("ORS walk directions failed for (%.4f, %.4f): %s", lat, lng, e)
        return None
    except httpx.HTTPStatusError as e:
        if e.response.status_code == HTTP_TOO_MANY_REQUESTS or (
            HTTP_5XX_START <= e.response.status_code < HTTP_5XX_END
        ):
            raise  # transient — let DAG retry handle it
        logger.warning("ORS walk directions failed for (%.4f, %.4f): %s", lat, lng, e)
        return None


async def _google_places_text(lat: float, lng: float) -> str:
    """Nearby-amenities text from Google Places; "" when it failed (Overpass fallback)."""
    types = [
        "supermarket",
        "park",
        "pharmacy",
        "convenience_store",
    ]
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    places_body = {
        "includedTypes": types,
        "maxResultCount": 5,
        "locationRestriction": {
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            "circle": {
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
                "center": {"latitude": lat, "longitude": lng},
                "radius": 1000.0,
            }
        },
    }
    try:
        async with cached_async_client(timeout=15.0) as client:

            async def _fetch_places():
                resp = await client.post(
                    GOOGLE_MAPS_PLACES_URL,
                    headers={
                        "X-Goog-Api-Key": settings.google_maps_api_key,
                        "X-Goog-FieldMask": "places.displayName,places.types,places.location",
                        "Content-Type": "application/json",
                    },
                    json=places_body,
                )
                resp.raise_for_status()
                return resp.json()

            data = await with_cache("POST", GOOGLE_MAPS_PLACES_URL, body=places_body, fetch=_fetch_places)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == HTTP_TOO_MANY_REQUESTS or (HTTP_5XX_START <= status < HTTP_5XX_END):
            raise  # transient — let DAG retry handle it
        logger.warning("Google Places API failed (%s), falling back to Overpass", status)
        return ""
    except httpx.RequestError:
        raise  # transient — let DAG retry handle it
    except (KeyError, IndexError) as e:
        logger.warning("Google Places API failed (%s), falling back to Overpass", e)
        return ""
    return _format_places(data, lat, lng)


async def _nearby_amenities(lat: float, lng: float) -> str:
    """Walkable-amenities summary for a property; Google first, Overpass fallback."""
    places = await _google_places_text(lat, lng)
    if places:
        return places

    # Fallback: OpenStreetMap Overpass API (free, no key)
    overpass_url = "https://overpass-api.de/api/interpreter"
    overpass_query = (
        f"[out:json][timeout:10];"
        f'(node(around:1000,{lat},{lng})["shop"~"supermarket|convenience"];'
        f'node(around:1000,{lat},{lng})["amenity"="pharmacy"];'
        f'way(around:1000,{lat},{lng})["leisure"="park"];'
        f");out center 5;"
    )
    overpass_params = {"data": overpass_query}
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
    except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException):
        raise  # transient — let DAG retry handle it
    # lucidlint: ignore broad-except deliberate fallback — Overpass failure returns the partial places string
    except Exception as e:
        logger.warning("Overpass fallback failed: %s: %s", type(e).__name__, e)
        return places

    return places


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _format_places(data: dict, lat: float, lng: float) -> str:
    """Format Google Places response into a human-readable string."""
    google_places = data.get("places", [])
    if not google_places:
        return ""
    origin = GeoPoint(lat, lng)
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
            dist_km = origin.distance_km_to(GeoPoint(place_lat, place_lng))
            walk_min = max(1, round(dist_km / WALKING_SPEED_KMH * MINUTES_PER_HOUR))
            hits.append((walk_min, f"{name} ({walk_min}m)"))
        else:
            hits.append((999, name))
    hits.sort(key=lambda x: x[0])
    return " | ".join(name for _, name in hits[:5])


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _format_overpass(data: dict, lat: float, lng: float) -> str:
    """Format Overpass API response into a human-readable string."""
    elements = data.get("elements", [])
    origin = GeoPoint(lat, lng)
    hits = []
    for e in elements:
        tags = e.get("tags", {})
        name = tags.get("name", "")
        if not name:
            continue
        e_lat = e.get("lat") or (e.get("center") or {}).get("lat")
        e_lng = e.get("lon") or (e.get("center") or {}).get("lon")
        if e_lat is not None and e_lng is not None:
            dist_km = origin.distance_km_to(GeoPoint(e_lat, e_lng))
            walk_min = max(1, round(dist_km / WALKING_SPEED_KMH * MINUTES_PER_HOUR))
            hits.append((walk_min, f"{name} ({walk_min}m)"))
        else:
            hits.append((999, name))
    hits.sort(key=lambda x: x[0])
    return " | ".join(name for _, name in hits[:5])


def _plausible_walk(minutes) -> bool:
    """True when the walk time is real and within a plausible range."""
    return minutes is not None and 0 < minutes <= MAX_PLAUSIBLE_WALK_MINUTES


async def _walk_to_town_minutes(
    origin: GeoPoint,
    town: str,
    extract_town_centre,
    walk_duration,
    reverse_geocode,
) -> int | None:
    """Best walk time to the town centre, or None.

    The address-derived town is tried first; if that gives an implausible
    result, the property coordinates are reverse-geocoded to find the
    actual nearest town.  Implausible values are discarded at the end.
    """
    lat, lng = origin.lat, origin.lon
    walk_to_town_minutes = None
    if town:
        town_centre = await extract_town_centre(lat, lng, town)
        if town_centre:
            walk_to_town_minutes = await walk_duration(lat, lng, town_centre)

    # If the address-based town failed or gave an implausible result, try
    # reverse-geocoding the property coordinates to find the actual nearest town.
    if not _plausible_walk(walk_to_town_minutes):
        rev_centre = await reverse_geocode(lat, lng)
        if rev_centre:
            rev_minutes = await walk_duration(lat, lng, rev_centre)
            if _plausible_walk(rev_minutes):
                walk_to_town_minutes = rev_minutes
            # If reverse geocode also failed to produce a valid time, leave
            # the original walk_to_town_minutes as-is (may be None or invalid).
    return walk_to_town_minutes if _plausible_walk(walk_to_town_minutes) else None



# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def enrich_walkability(
    lat: float,
    lng: float,
    address: str,
    fns: WalkabilityFns | None = None,
) -> dict[str, Any]:
    """Walk time to town centre + nearby amenities for a property.

    ``fns`` carries the function-param injection seams for tests
    (docs/testing-standards.md — no monkeypatching); None fields mean the
    real API-backed implementations.
    """
    fns = fns or WalkabilityFns()
    extract_town_centre = fns.extract_town_centre or _extract_town_centre
    walk_duration = fns.walk_duration or _walk_duration
    reverse_geocode = fns.reverse_geocode or _find_town_centre_by_reverse_geocode
    nearby_amenities = fns.nearby_amenities or _nearby_amenities

    town = extract_town(address)
    walk_to_town_minutes = await _walk_to_town_minutes(
        GeoPoint(lat, lng), town, extract_town_centre, walk_duration, reverse_geocode
    )
    amenities = await nearby_amenities(lat, lng)

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        "walk_to_town": {"value": walk_to_town_minutes, "unit": "minute"} if walk_to_town_minutes is not None else None,
        "amenities": amenities,
    }
