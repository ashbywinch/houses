"""Walk time to town centre and nearby amenities."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from houses import apis
from houses.geopoint import GeoPoint
from houses.location import PropertyLocation, WalkabilityFns

logger = logging.getLogger(__name__)


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


# lucidlint: ignore data-clump (lat, lng) is enrich_walkability's public signature — houses/services.py and the
async def _extract_town_centre(lat: float, lng: float, town: str) -> GeoPoint | None:
    """Resolve a town name to coordinates, used for walkability enrichment."""
    loc = await PropertyLocation.from_town(town)
    return loc.coordinates.value_or_none()


async def _find_town_centre_by_reverse_geocode(lat: float, lng: float) -> GeoPoint | None:
    """The nearest town's centre for a set of coordinates (ORS Pelias reverse)."""
    return await apis.ors.reverse_geocode(lat, lng)


async def _walk_duration(
    lat: float,
    lng: float,
    town_centre: GeoPoint,
    *,
    _client_factory=None,
) -> int | None:
    try:
        return await apis.ors.directions(
            GeoPoint(lat, lng), town_centre, mode="foot-walking",
            _client_factory=_client_factory,
        )
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
    try:
        results = await apis.places.search_nearby(lat, lng, types=types)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == HTTP_TOO_MANY_REQUESTS or (HTTP_5XX_START <= status < HTTP_5XX_END):
            raise  # transient — let DAG retry handle it
        logger.warning("Google Places API failed (%s), falling back to Overpass", status)
        return ""
    return _format_places(results, lat, lng)


async def _nearby_amenities(lat: float, lng: float) -> str:
    """Walkable-amenities summary for a property; Google first, Overpass fallback."""
    places = await _google_places_text(lat, lng)
    if places:
        return places

    # Fallback: OpenStreetMap Overpass API (free, no key)
    overpass_query = (
        f"[out:json][timeout:10];"
        f'(node(around:1000,{lat},{lng})["shop"~"supermarket|convenience"];'
        f'node(around:1000,{lat},{lng})["amenity"="pharmacy"];'
        f'way(around:1000,{lat},{lng})["leisure"="park"];'
        f");out center 5;"
    )
    try:
        response = await apis.overpass.query(overpass_query)
        places = _format_overpass(response, lat, lng)
    except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException):
        raise  # transient — let DAG retry handle it
    # lucidlint: ignore broad-except deliberate fallback — Overpass failure returns the partial places string
    except Exception as e:
        logger.warning("Overpass fallback failed: %s: %s", type(e).__name__, e)
        return places

    return places


def _format_places(places: list[apis.PlacesResult], lat: float, lng: float) -> str:
    """Format Google Places results into a human-readable string."""
    if not places:
        return ""
    origin = GeoPoint(lat, lng)
    hits = []
    for place in places:
        place_types = set(place.types)
        if place_types & {
            "transit_station",
            "bus_stop",
            "bus_station",
            "locality",
            "administrative_area_level_3",
            "administrative_area_level_4",
        }:
            continue
        name = place.name
        place_lat = place.latitude
        place_lng = place.longitude
        if place_lat is not None and place_lng is not None:
            dist_km = origin.distance_km_to(GeoPoint(place_lat, place_lng))
            walk_min = max(1, round(dist_km / WALKING_SPEED_KMH * MINUTES_PER_HOUR))
            hits.append((walk_min, f"{name} ({walk_min}m)"))
        else:
            hits.append((999, name))
    hits.sort(key=lambda x: x[0])
    return " | ".join(name for _, name in hits[:5])


def _format_overpass(data: apis.OverpassResponse, lat: float, lng: float) -> str:
    """Format Overpass API response into a human-readable string."""
    elements = data.elements
    origin = GeoPoint(lat, lng)
    hits = []
    for element in elements:
        name = element.tags.get("name", "")
        if not name:
            continue
        e_lat = element.latitude or (element.center[0] if element.center else None)
        e_lng = element.longitude or (element.center[1] if element.center else None)
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



@dataclass(frozen=True)
class _WalkToTown:
    """The walk-to-town summary entry of the walkability payload."""

    value: int
    unit: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the wire shape (coding-standards.md)
        return dict(value=self.value, unit=self.unit)


@dataclass(frozen=True)
class WalkabilityPayload:
    """The walkability node value: walk-to-town summary plus amenities text."""

    walk_to_town: _WalkToTown | None
    amenities: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the wire shape (coding-standards.md)
        return dict(
            walk_to_town=self.walk_to_town.to_dict() if self.walk_to_town is not None else None,
            amenities=self.amenities,
        )


async def enrich_walkability(
    lat: float,
    lng: float,
    address: str,
    fns: WalkabilityFns | None = None,
) -> WalkabilityPayload:
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

    walk_to_town = _WalkToTown(value=walk_to_town_minutes, unit="minute") if walk_to_town_minutes is not None else None
    return WalkabilityPayload(walk_to_town=walk_to_town, amenities=amenities)
