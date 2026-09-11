"""Walk time to town centre and nearby amenities."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

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


# lucidlint: ignore data-clump (lat, lng) is enrich_walkability's public signature — houses/services.py and the
async def _extract_town_centre(lat: float, lng: float, town: str) -> GeoPoint | None:
    """Resolve a town name to coordinates, used for walkability enrichment."""
    loc = await PropertyLocation.from_town(town)
    return loc.coordinates.value_or_none()


async def _find_town_centre_by_reverse_geocode(lat: float, lng: float) -> GeoPoint | None:
    """Use ORS Pelias reverse geocode to find the nearest town and its centre."""

    rev_url = ORS_GEOCODE_URL.replace("/search", "/reverse")
    params = _ReverseGeocodeParamsJson(point_lat=lat, point_lon=lng, size=1, boundary_country="GBR")

    cached = get_cached("GET", rev_url, params, None)
    if cached is not None:
        data = cached
    else:
        try:
            async with cached_async_client(timeout=10.0) as client:
                resp = await client.get(rev_url, params=params.to_dict())
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", rev_url, params, None, data)
        except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException):
            raise  # transient — let DAG retry handle it
        # lucidlint: ignore broad-except deliberate fallback — reverse-geocode failure returns None
        except Exception:
            logger.warning("ORS reverse geocode failed for (%.4f, %.4f)", lat, lng, exc_info=True)
            return None

    features = _GeocodeResponseJson.from_dict(data).features
    if not features:
        return None
    props = features[0].properties
    town = props.locality or props.borough
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
    body = _ORSWalkBody(coordinates=[origin, dest])
    try:
        async with cached_async_client(timeout=15.0) as client:

            async def _fetch():
                resp = await client.post(
                    ORS_WALKING_URL,
                    headers={
                        "Authorization": settings.ors_api_key,
                        "Content-Type": "application/json",
                    },
                    json=body.to_dict(),
                )
                resp.raise_for_status()
                return resp.json()

            data = await with_cache("POST", ORS_WALKING_URL, body=body, fetch=_fetch)
        response = _DirectionsResponseJson.from_dict(data)
        return round(response.routes[0].summary.duration / SECONDS_PER_MINUTE)
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
    places_body = _PlacesBody(
        included_types=types,
        max_result_count=5,
        location_restriction=_PlacesLocationRestriction(
            circle=_PlacesCircle(center=_PlacesCircleCenter(latitude=lat, longitude=lng), radius=1000.0)
        ),
    )
    try:
        async with cached_async_client(timeout=15.0) as client:

            # lucidlint: ignore duplicate twin POST wrapper of _fetch above — the shared cache logic already lives in
            async def _fetch_places():
                resp = await client.post(
                    GOOGLE_MAPS_PLACES_URL,
                    headers={
                        "X-Goog-Api-Key": settings.google_maps_api_key,
                        "X-Goog-FieldMask": "places.displayName,places.types,places.location",
                        "Content-Type": "application/json",
                    },
                    json=places_body.to_dict(),
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
    # lucidlint: ignore duplicate-block the RequestError and KeyError/IndexError handlers intentionally share the same
    except httpx.RequestError:
        raise  # transient — let DAG retry handle it
    except (KeyError, IndexError) as e:
        logger.warning("Google Places API failed (%s), falling back to Overpass", e)
        return ""
    return _format_places(_PlacesResponseJson.from_dict(data), lat, lng)


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
    overpass_params = _OverpassParamsJson(data=overpass_query)
    try:
        async with cached_async_client(timeout=15.0) as client:

            async def _fetch_overpass():
                resp = await client.get(
                    overpass_url,
                    params=overpass_params.to_dict(),
                    headers={"Accept": "application/json", "User-Agent": "HousesApp/1.0"},
                )
                resp.raise_for_status()
                return resp.json()

            data = await with_cache("GET", overpass_url, params=overpass_params, fetch=_fetch_overpass)
        places = _format_overpass(_OverpassResponseJson.from_dict(data), lat, lng)
    except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException):
        raise  # transient — let DAG retry handle it
    # lucidlint: ignore broad-except deliberate fallback — Overpass failure returns the partial places string
    except Exception as e:
        logger.warning("Overpass fallback failed: %s: %s", type(e).__name__, e)
        return places

    return places


def _format_places(data: _PlacesResponseJson, lat: float, lng: float) -> str:
    """Format Google Places response into a human-readable string."""
    google_places = data.places
    if not google_places:
        return ""
    origin = GeoPoint(lat, lng)
    hits = []
    for place in google_places:
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
        name = place.display_name.text
        place_lat = place.location.latitude
        place_lng = place.location.longitude
        if place_lat is not None and place_lng is not None:
            dist_km = origin.distance_km_to(GeoPoint(place_lat, place_lng))
            walk_min = max(1, round(dist_km / WALKING_SPEED_KMH * MINUTES_PER_HOUR))
            hits.append((walk_min, f"{name} ({walk_min}m)"))
        else:
            hits.append((999, name))
    hits.sort(key=lambda x: x[0])
    return " | ".join(name for _, name in hits[:5])


def _format_overpass(data: _OverpassResponseJson, lat: float, lng: float) -> str:
    """Format Overpass API response into a human-readable string."""
    elements = data.elements
    origin = GeoPoint(lat, lng)
    hits = []
    for element in elements:
        name = element.tags.name
        if not name:
            continue
        e_lat = element.lat or (element.center.lat if element.center else None)
        e_lng = element.lon or (element.center.lon if element.center else None)
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
class _ReverseGeocodeParamsJson:
    """The ORS reverse-geocode query params — {point.lat, point.lon, size, boundary.country}."""

    point_lat: float
    point_lon: float
    size: int
    boundary_country: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the ORS query-param names (coding-standards.md)
        return {
            "point.lat": self.point_lat,
            "point.lon": self.point_lon,
            "size": self.size,
            "boundary.country": self.boundary_country,
        }


@dataclass(frozen=True)
class _OverpassParamsJson:
    """The Overpass API query-string params — the {data} wire shape."""

    data: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return dict(data=self.data)


@dataclass(frozen=True)
class _GeocodeResponseJson:
    """The ORS (Pelias) reverse-geocode response root — the {features} wire shape."""

    features: list[_GeocodeFeatureJson]

    @classmethod
    def from_dict(cls, raw: dict) -> _GeocodeResponseJson:
        return cls(features=[_GeocodeFeatureJson.from_dict(f) for f in raw.get("features") or []])


@dataclass(frozen=True)
class _GeocodeFeatureJson:
    """An ORS geocode feature — the {properties} wire shape."""

    properties: _GeocodePropertiesJson

    @classmethod
    def from_dict(cls, raw: dict) -> _GeocodeFeatureJson:
        return cls(properties=_GeocodePropertiesJson.from_dict(raw.get("properties", {})))


@dataclass(frozen=True)
class _GeocodePropertiesJson:
    """The ORS geocode feature properties — the {locality, borough} wire shape."""

    locality: str | None
    borough: str | None

    @classmethod
    def from_dict(cls, raw: dict) -> _GeocodePropertiesJson:
        return cls(locality=raw.get("locality"), borough=raw.get("borough"))


@dataclass(frozen=True)
class _DirectionsResponseJson:
    """The ORS walking-directions response root — the {routes} wire shape."""

    routes: list[_DirectionsRouteJson]

    @classmethod
    def from_dict(cls, raw: dict) -> _DirectionsResponseJson:
        return cls(routes=[_DirectionsRouteJson.from_dict(r) for r in raw["routes"]])


@dataclass(frozen=True)
class _DirectionsRouteJson:
    """An ORS directions route — the {summary} wire shape."""

    summary: _DirectionsSummaryJson

    @classmethod
    def from_dict(cls, raw: dict) -> _DirectionsRouteJson:
        return cls(summary=_DirectionsSummaryJson.from_dict(raw["summary"]))


@dataclass(frozen=True)
class _DirectionsSummaryJson:
    """The ORS route summary — the {duration} wire shape."""

    duration: float

    @classmethod
    def from_dict(cls, raw: dict) -> _DirectionsSummaryJson:
        return cls(duration=raw["duration"])


@dataclass(frozen=True)
class _PlacesResponseJson:
    """The Google Places nearby-search response root — the {places} wire shape."""

    places: list[_PlacesPlaceJson]

    @classmethod
    def from_dict(cls, raw: dict) -> _PlacesResponseJson:
        return cls(places=[_PlacesPlaceJson.from_dict(p) for p in raw.get("places") or []])


@dataclass(frozen=True)
class _PlacesPlaceJson:
    """A Google Places place — the {types, displayName, location} wire shape."""

    types: list[str]
    display_name: _PlacesDisplayNameJson
    location: _PlacesLocationJson

    @classmethod
    def from_dict(cls, raw: dict) -> _PlacesPlaceJson:
        return cls(
            types=raw.get("types", []),
            display_name=_PlacesDisplayNameJson.from_dict(raw.get("displayName", {})),
            location=_PlacesLocationJson.from_dict(raw.get("location", {})),
        )


@dataclass(frozen=True)
class _PlacesDisplayNameJson:
    """A Google Places displayName — the {text} wire shape."""

    text: str

    @classmethod
    def from_dict(cls, raw: dict) -> _PlacesDisplayNameJson:
        return cls(text=raw.get("text", "Unknown"))


@dataclass(frozen=True)
class _PlacesLocationJson:
    """A Google Places location — the {latitude, longitude} wire shape."""

    latitude: float | None
    longitude: float | None

    @classmethod
    def from_dict(cls, raw: dict) -> _PlacesLocationJson:
        return cls(latitude=raw.get("latitude"), longitude=raw.get("longitude"))


@dataclass(frozen=True)
class _OverpassResponseJson:
    """The Overpass API response root — the {elements} wire shape."""

    elements: list[_OverpassElementJson]

    @classmethod
    def from_dict(cls, raw: dict) -> _OverpassResponseJson:
        return cls(elements=[_OverpassElementJson.from_dict(e) for e in raw.get("elements", [])])


@dataclass(frozen=True)
class _OverpassElementJson:
    """An Overpass element — the {tags, lat, lon, center} wire shape."""

    tags: _OverpassTagsJson
    lat: float | None
    lon: float | None
    center: _OverpassCenterJson | None

    @classmethod
    def from_dict(cls, raw: dict) -> _OverpassElementJson:
        center = raw.get("center")
        return cls(
            tags=_OverpassTagsJson.from_dict(raw.get("tags", {})),
            lat=raw.get("lat"),
            lon=raw.get("lon"),
            center=_OverpassCenterJson.from_dict(center) if center else None,
        )


@dataclass(frozen=True)
class _OverpassTagsJson:
    """An Overpass element's tags — the {name} wire shape."""

    name: str

    @classmethod
    def from_dict(cls, raw: dict) -> _OverpassTagsJson:
        return cls(name=raw.get("name", ""))


@dataclass(frozen=True)
class _OverpassCenterJson:
    """An Overpass way's center — the {lat, lon} wire shape."""

    lat: float | None
    lon: float | None

    @classmethod
    def from_dict(cls, raw: dict) -> _OverpassCenterJson:
        return cls(lat=raw.get("lat"), lon=raw.get("lon"))


@dataclass(frozen=True)
class _ORSWalkBody:
    """ORS walking-directions request body (wire shape)."""

    coordinates: list[list[float]]

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return dict(coordinates=self.coordinates)


@dataclass(frozen=True)
class _PlacesCircleCenter:
    """Google Places circle-center wire shape."""

    latitude: float
    longitude: float

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the wire shape (coding-standards.md)
        return dict(latitude=self.latitude, longitude=self.longitude)


@dataclass(frozen=True)
class _PlacesCircle:
    """Google Places circular location restriction (wire shape)."""

    center: _PlacesCircleCenter
    radius: float

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the wire shape (coding-standards.md)
        return dict(center=self.center.to_dict(), radius=self.radius)


@dataclass(frozen=True)
class _PlacesLocationRestriction:
    """Google Places location restriction (wire shape)."""

    circle: _PlacesCircle

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return dict(circle=self.circle.to_dict())


@dataclass(frozen=True)
class _PlacesBody:
    """Google Places nearby-search request body (wire shape)."""

    included_types: list[str]
    max_result_count: int
    location_restriction: _PlacesLocationRestriction

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the wire shape (coding-standards.md)
        return dict(
            includedTypes=self.included_types,
            maxResultCount=self.max_result_count,
            locationRestriction=self.location_restriction.to_dict(),
        )


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
