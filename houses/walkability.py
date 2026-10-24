"""Walk time to town centre and nearby amenities."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

import httpx
from pint import Quantity

from houses import apis
from houses.geopoint import GeoPoint
from houses.location import PropertyLocation, WalkabilityFns
from serialization import QuantityValue, SerializableAsDict

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
class _WalkabilityLimits:
    """Walkability enrichment tunables: the HTTP retry band for API
    calls, the plausibility bound on walk times, and the shape of the
    amenities summary (how many places it names, where a placeless one
    sorts)."""

    HTTP_TOO_MANY_REQUESTS = 429
    HTTP_5XX_START = 500
    HTTP_5XX_END = 600
    MAX_PLAUSIBLE_WALK_MINUTES = 180
    SUMMARY_PLACES = 5
    NO_POINT_SORT_KEY = 999


# walking pace as a Quantity — the km→minutes math goes through pint
WALKING_SPEED = Quantity(5, "km/hour")


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
            GeoPoint(lat, lng),
            town_centre,
            mode="foot-walking",
            _client_factory=_client_factory,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == _WalkabilityLimits.HTTP_TOO_MANY_REQUESTS or (
            _WalkabilityLimits.HTTP_5XX_START <= e.response.status_code < _WalkabilityLimits.HTTP_5XX_END
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
        if status == _WalkabilityLimits.HTTP_TOO_MANY_REQUESTS or (
            _WalkabilityLimits.HTTP_5XX_START <= status < _WalkabilityLimits.HTTP_5XX_END
        ):
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
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as e:
        logger.warning("Overpass fallback failed: %s: %s", type(e).__name__, e)
        return places

    return places


def _amenity_summary(origin: GeoPoint, named: Iterable[tuple[str, GeoPoint | None]]) -> str:
    """The nearest destinations as one line: nearest first, at most five.

    Both sources — Google Places and the Overpass fallback — agree on how a
    place becomes a line, and differ only in where its name and point come
    from. A place with no point cannot be timed, so it sorts last.
    """
    hits: list[tuple[int, str]] = []
    for name, point in named:
        if point is None:
            hits.append((_WalkabilityLimits.NO_POINT_SORT_KEY, name))
            continue
        # GeoPoint.distance_km_to is a bare-km wire value; the pace math is
        # pint, and the minutes int is the serialization boundary
        walk_minutes_q = (Quantity(origin.distance_km_to(point), "km") / WALKING_SPEED).to("minute")
        walk_min = max(1, round(walk_minutes_q.magnitude))  # one step away is still a minute
        hits.append((walk_min, f"{name} ({walk_min}m)"))
    hits.sort(key=lambda x: x[0])
    return " | ".join(name for _, name in hits[: _WalkabilityLimits.SUMMARY_PLACES])


def _format_places(places: list[apis.PlacesResult], lat: float, lng: float) -> str:
    """The amenities summary from Google Places — destinations only."""
    destinations = ((place.name, place.location) for place in places if place.is_destination())
    return _amenity_summary(GeoPoint(lat, lng), destinations)


def _format_overpass(data: apis.OverpassResponse, lat: float, lng: float) -> str:
    """The amenities summary from the Overpass fallback — named elements only."""
    named = ((name, element.location()) for element in data.elements if (name := element.tags.get("name", "")))
    return _amenity_summary(GeoPoint(lat, lng), named)


def _plausible_walk(minutes) -> bool:
    """True when the walk time is real and within a plausible range."""
    return minutes is not None and 0 < minutes <= _WalkabilityLimits.MAX_PLAUSIBLE_WALK_MINUTES


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


class Walkability(SerializableAsDict):
    """The walkability node value: walk-to-town time plus amenities text,
    serialized once at the node-value edge by ``to_dict``."""

    walk_to_town: QuantityValue | None = None
    amenities: str


async def enrich_walkability(
    lat: float,
    lng: float,
    address: str,
    fns: WalkabilityFns | None = None,
) -> Walkability:
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

    walk_to_town = Quantity(walk_to_town_minutes, "minute") if walk_to_town_minutes is not None else None
    return Walkability(walk_to_town=walk_to_town, amenities=amenities)
