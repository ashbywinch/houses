"""Walk time to town centre and nearby amenities."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from houses.api_cache import cached_async_client, with_cache
from houses.config import settings
from houses.geo import GeoPoint
from houses.location import PropertyLocation

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


def _extract_town(address: str) -> str:
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
    from houses.api_cache import cached_async_client, get_cached, set_cached

    rev_url = ORS_GEOCODE_URL.replace("/search", "/reverse")
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
        except Exception:
            return None

    features = data.get("features", [])
    if not features:
        return None
    props = features[0].get("properties", {})
    town = props.get("locality") or props.get("borough") or props.get("county")
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
        return round(data["routes"][0]["summary"]["duration"] / 60)
    except (KeyError, IndexError) as e:
        logger.warning("ORS walk directions failed for (%.4f, %.4f): %s", lat, lng, e)
        return None
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429 or (500 <= e.response.status_code < 600):
            raise  # transient — let DAG retry handle it
        logger.warning("ORS walk directions failed for (%.4f, %.4f): %s", lat, lng, e)
        return None


async def _nearby_amenities(lat: float, lng: float) -> str:
    types = [
        "supermarket",
        "park",
        "pharmacy",
        "convenience_store",
    ]
    places = ""
    google_failed = False

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
        result = _format_places(data, lat, lng)
        if result:
            return result
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 429 or (500 <= status < 600):
            raise  # transient — let DAG retry handle it
        logger.warning("Google Places API failed (%s), falling back to Overpass", status)
        google_failed = True
    except httpx.RequestError:
        raise  # transient — let DAG retry handle it
    except (KeyError, IndexError) as e:
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
            walk_min = max(1, round(dist_km / 5 * 60))
            hits.append((walk_min, f"{name} ({walk_min}m)"))
        else:
            hits.append((999, name))
    hits.sort(key=lambda x: x[0])
    return " | ".join(name for _, name in hits[:5])


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

    if town:
        town_centre = await _extract_town_centre(lat, lng, town)
        if town_centre:
            walk_to_town_minutes = await _walk_duration(lat, lng, town_centre)

    # If the address-based town failed or gave an implausible result, try
    # reverse-geocoding the property coordinates to find the actual nearest town.
    if walk_to_town_minutes is None or walk_to_town_minutes <= 0 or walk_to_town_minutes > 180:
        rev_centre = await _find_town_centre_by_reverse_geocode(lat, lng)
        if rev_centre:
            rev_minutes = await _walk_duration(lat, lng, rev_centre)
            if rev_minutes is not None and 0 < rev_minutes <= 180:
                walk_to_town_minutes = rev_minutes
            # If reverse geocode also failed to produce a valid time, leave
            # the original walk_to_town_minutes as-is (may be None or invalid).

    amenities = await _nearby_amenities(lat, lng)

    # Sanitize walk time: ignore impossible values
    if walk_to_town_minutes is not None and (walk_to_town_minutes <= 0 or walk_to_town_minutes > 180):
        walk_to_town_minutes = None

    return {
        "walk_to_town_minutes": walk_to_town_minutes,
        "amenities": amenities,
    }
