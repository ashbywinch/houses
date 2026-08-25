"""PropertyLocation — where a property is on the map, possibly unresolved."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from typing import Any

import httpx

from dag.attempt import Attempt
from houses.api_cache import cached_async_client, get_cached, set_cached
from houses.geopoint import GeoPoint
from houses.services_provider import get_services
from houses.settings import settings

logger = logging.getLogger(__name__)
HTTP_TOO_MANY_REQUESTS = 429
HTTP_NOT_FOUND = 404

# ── Geocoding API state (per-request via Services) ─────────────


class _GeoState:
    google_exhausted: bool = False
    ors_geo_exhausted: bool = False
    nominatim_exhausted: bool = False
    nominatim_last_call: float = 0.0


def get_geo_state(*, services: Any | None = None) -> _GeoState:
    """Return the per-request geocoder state, lazily created on the services container."""
    svc = services or get_services()
    if svc.geo_state is None:
        svc.geo_state = _GeoState()
    return svc.geo_state


# ── URL constants ────────────────────────────────────────────────

POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"
OUTCODES_IO_URL = "https://api.postcodes.io/outcodes"
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# ── Regex patterns ───────────────────────────────────────────────

_OUTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")
_END_PC_RE = re.compile(r",\s*[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?\s*$", re.IGNORECASE)
_TOWN_SUFFIXES = re.compile(
    r"\s+(Station Area|Station|Area|Village|Town Centre|Centre|Villlage|Park|Business Park|Bottom)$",
    re.IGNORECASE,
)

# ── In-memory geocode cache (per-request via Services) ─────────


# lucidlint: ignore record-shape keyed geocode cache map (variable keys), not a fixed record shape
def _geo_cache(*, services: Any | None = None) -> dict:
    """Return the per-request geocode cache, lazily created on the services container."""
    svc = services or get_services()
    if svc.geo_cache is None:
        svc.geo_cache = {}
    return svc.geo_cache


@dataclass(frozen=True)
class ReverseGeocodeOptions:
    """HTTP options for reverse-geocoding: API key, cache, and client seams.

    The cache/client seams default to the module implementations so tests
    never monkeypatch ``houses.location`` globals.
    """

    api_key: str | None = None
    get_cached_fn: Callable[[str, str, dict[str, Any] | None, str | None], dict[str, Any] | None] | None = None
    set_cached_fn: Callable[[str, str, dict[str, Any] | None, str | None, dict[str, Any]], None] | None = None
    client_factory: Callable[..., AbstractAsyncContextManager[Any]] | None = None


@dataclass(frozen=True)
class HouseLocationInputs:
    """A property's location sources, best first: the address/postcode plus
    any measured or approximate coordinates from the data sheet."""

    postcode: str
    address: str
    actual_latitude: float | None = None
    actual_longitude: float | None = None
    approx_lat: float | None = None
    approx_lng: float | None = None




def _cache_result(key: str, result: Attempt[GeoPoint], *, services: Any | None = None) -> None:
    """Store a geocode result in the per-request cache."""
    cache = _geo_cache(services=services)
    cache[key] = result


@dataclass(frozen=True)
class PropertyLocation:
    """Where a property is on the map, possibly unresolved.

    Create with a postcode and/or address, then ``await resolve()``
    to get coordinates.
    """

    postcode: str = ""
    address: str = ""
    coordinates: Attempt[GeoPoint] = Attempt.pending()

    @staticmethod
    def _upgrade_address(address: str, postcode: str) -> str:
        """Replace a trailing outcode in *address* with the full *postcode*.

        When the address ends with an outcode (e.g. ``"Grand Drive, London, SW20"``)
        and we have a full postcode (e.g. ``"SW20 9NB"``), this returns the address
        with the outcode replaced: ``"Grand Drive, London, SW20 9NB"``.
        This prevents ambiguous street names from geocoding to the wrong location.

        Returns the original address unchanged if:
        - *postcode* is empty or itself an outcode
        - The address doesn't end with what looks like a trailing postcode
        - The trailing part is already a full postcode (not just an outcode)
        """
        if not address or not postcode or _OUTCODE_RE.match(postcode.strip().upper()):
            return address
        m = _END_PC_RE.search(address)
        if not m:
            return address
        trailing = m.group(0).strip(", ").strip()
        if not _OUTCODE_RE.match(trailing.upper()):
            # Trailing part is already a full postcode or not a postcode at all
            return address
        base = _END_PC_RE.sub("", address).strip()
        return f"{base}, {postcode}"

    async def resolve(self, *, services: Any | None = None) -> PropertyLocation:
        """Resolve address first, then postcode.

        When the address ends with an outcode but we have a full postcode,
        the address is upgraded to include the full postcode before geocoding
        (see ``_upgrade_address``).  This prevents ambiguous street names
        (e.g. ``"Grand Drive, London, SW20"`` — there are many Grand Drives
        across UK outcodes) from returning wrong coordinates.

        Only makes API calls when coordinates are still pending.
        Returns a new ``PropertyLocation`` with ``coordinates`` populated.
        """
        if not self.coordinates.pending:
            return self

        address = self._upgrade_address(self.address, self.postcode)
        result = await geocode_address(address, services=services)
        if result.succeeded:
            return replace(self, coordinates=result)
        result = await _geocode_postcode(self.postcode, services=services)
        return replace(self, coordinates=result)

    def resolved(self, point: GeoPoint, source: str) -> PropertyLocation:
        """Return a new PropertyLocation with coordinates pre-set from a known value.

        Useful when the sheet already has coordinates and re-geocoding is unnecessary.
        """
        return replace(self, coordinates=Attempt.succeeded(point))

    @classmethod
    async def from_town(cls, town: str, *, services: Any | None = None) -> PropertyLocation:
        """Resolve a town name to a PropertyLocation via the injected geocoder.

        Tries the service-layer geocoder (Google Maps → ORS → Nominatim).
        Strips common suffixes (``Station Area``, ``Village``, etc.) as
        a final fallback if the geocoder returns no result.
        """
        key = town.strip().upper()
        if not key:
            return cls(coordinates=Attempt.impossible("empty town name"))

        svc = services or get_services()
        result = await svc.geocoder.geocode_address(f"{town}, UK")
        coords = result.value_or_none()
        if coords is not None:
            return cls(coordinates=Attempt.succeeded(coords))

        # Try stripping suffixes like "Station Area" → "Maidenhead"
        stripped = _TOWN_SUFFIXES.sub("", town).strip()
        if stripped and stripped.upper() != key:
            return await cls.from_town(stripped, services=services)

        return cls(coordinates=Attempt.impossible("all geocoders failed"))


# ── Private helpers ──────────────────────────────────────────────


async def geocode(postcode: str, *, services: Any | None = None) -> Attempt[GeoPoint]:
    """Geocode a UK postcode — public entry point for one-shot lookups."""
    return await _geocode_postcode(postcode, services=services)


async def _geocode_nominatim(query: str, *, services: Any | None = None) -> Attempt[GeoPoint]:
    """Geocode a place name via Nominatim (free, 1 req/sec max)."""
    if get_geo_state(services=services).nominatim_exhausted:
        return Attempt.impossible("rate limit exhausted")
    cache_key = f"nom::{query.strip().upper()}"
    cache = _geo_cache(services=services)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    clean = _END_PC_RE.sub("", query).strip()
    now = asyncio.get_event_loop().time()
    since_last = now - get_geo_state(services=services).nominatim_last_call
    if since_last < 1.0:
        await asyncio.sleep(1.0 - since_last)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    params = {"q": f"{clean}, UK", "format": "json", "limit": 1}
    cached = get_cached("GET", NOMINATIM_URL, params, None)
    if cached is not None:
        # Nominatim returns a JSON array of results — not a dict — so treat
        # the cached payload as Any, mirroring the fresh `resp.json()` path.
        data: Any = cached
        if data:
            lat = float(data[0]["lat"])
            lng = float(data[0]["lon"])
            gp = GeoPoint(lat, lng)
            result = Attempt.succeeded(gp)
            _cache_result(cache_key, result, services=services)
            return result
        return Attempt.impossible("no results")
    else:
        try:
            async with cached_async_client(timeout=10.0) as client:
                resp = await client.get(
                    NOMINATIM_URL,
                    params=params,
                    headers={"User-Agent": "HousesApp/1.0"},
                )
                get_geo_state(services=services).nominatim_last_call = asyncio.get_event_loop().time()
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", NOMINATIM_URL, params, None, data)
                if data:
                    lat = float(data[0]["lat"])
                    lng = float(data[0]["lon"])
                    gp = GeoPoint(lat, lng)
                    result = Attempt.succeeded(gp)
                    _cache_result(cache_key, result, services=services)
                    return result
                return Attempt.impossible("no results")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == HTTP_TOO_MANY_REQUESTS:
                get_geo_state(services=services).nominatim_exhausted = True
            logger.warning("Nominatim geocoding failed for %s (%s)", query, exc.response.status_code)
            return Attempt.impossible(f"HTTP {exc.response.status_code}")
        # lucidlint: ignore broad-except boundary — unexpected geocode failures convert to an impossible attempt
        except Exception:
            logger.warning("Nominatim geocoding failed for: %s", query)
            return Attempt.impossible("unexpected error")


async def _geocode_google(address: str, cache_key: str, *, services: Any | None = None) -> Attempt[GeoPoint] | None:
    """Geocode *address* via Google Maps; ``None`` means "try the next provider"."""
    if get_geo_state(services=services).google_exhausted:
        logger.debug("Skipping Google Maps — API quota exhausted")
        return None
    googlegeocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    params = {"address": f"{address}, UK", "key": settings.google_maps_api_key}
    cache_params = {"address": f"{address}, UK"}
    cached = get_cached("GET", googlegeocode_url, cache_params, None)
    if cached is not None:
        data = cached
        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            gp = GeoPoint(loc["lat"], loc["lng"])
            result = Attempt.succeeded(gp)
            _cache_result(cache_key, result, services=services)
            logger.info("Geocoded '%s' via google-maps (cached)", address)
            return result
        if data.get("status") == "OVER_QUERY_LIMIT":
            get_geo_state(services=services).google_exhausted = True
        logger.warning(
            "Google Maps cached result for '%s' rejected: status=%s msg=%s",
            address,
            data.get("status"),
            data.get("error_message", ""),
        )
        return None
    try:
        async with cached_async_client(timeout=10.0) as client:
            resp = await client.get(googlegeocode_url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "OK" and data.get("results"):
                set_cached("GET", googlegeocode_url, cache_params, None, data)
                loc = data["results"][0]["geometry"]["location"]
                gp = GeoPoint(loc["lat"], loc["lng"])
                result = Attempt.succeeded(gp)
                _cache_result(cache_key, result, services=services)
                logger.info("Geocoded '%s' via google-maps", address)
                return result
            if data.get("status") == "OVER_QUERY_LIMIT":
                get_geo_state(services=services).google_exhausted = True
            logger.warning(
                "Google Maps API response for '%s': status=%s msg=%s",
                address,
                data.get("status"),
                data.get("error_message", ""),
            )
    # lucidlint: ignore broad-except deliberate fallback — provider failure means try the next geocoder
    except Exception as exc:
        logger.warning("Google Maps geocoding failed for '%s': %s", address, exc)
        return None
    return None


async def _geocode_ors(address: str, cache_key: str, *, services: Any | None = None) -> Attempt[GeoPoint] | None:
    """Geocode *address* via ORS Pelias; ``None`` means "try the next provider"."""
    if get_geo_state(services=services).ors_geo_exhausted:
        return None
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    params = {"text": f"{address}, UK", "size": 1}
    cached = get_cached("GET", ORS_GEOCODE_URL, params, None)
    if cached is not None:
        data = cached
        features = data.get("features", [])
        if features:
            lng, lat = features[0]["geometry"]["coordinates"]
            gp = GeoPoint(lat, lng)
            result = Attempt.succeeded(gp)
            _cache_result(cache_key, result, services=services)
            logger.info("Geocoded '%s' via ors-pelias (cached) → (%s, %s)", address, f"{lat:.4f}", f"{lng:.4f}")
            return result
        return None
    try:
        async with cached_async_client(timeout=10.0) as client:
            resp = await client.get(
                ORS_GEOCODE_URL,
                params=params,
                headers={"Authorization": settings.ors_api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            set_cached("GET", ORS_GEOCODE_URL, params, None, data)
            features = data.get("features", [])
            if features:
                lng, lat = features[0]["geometry"]["coordinates"]
                gp = GeoPoint(lat, lng)
                result = Attempt.succeeded(gp)
                _cache_result(cache_key, result, services=services)
                logger.info("Geocoded '%s' via ors-pelias → (%s, %s)", address, f"{lat:.4f}", f"{lng:.4f}")
                return result
            logger.warning("ORS returned no features for '%s'", address)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (403, 429):
            get_geo_state(services=services).ors_geo_exhausted = True
        logger.warning("ORS geocoding failed for '%s': HTTP %s", address, exc.response.status_code)
        return None
    # lucidlint: ignore broad-except deliberate fallback — provider failure means try the next geocoder
    except Exception as exc:
        logger.warning("ORS geocoding failed for '%s': %s", address, exc)
        return None
    return None




async def geocode_address(address: str, *, services: Any | None = None) -> Attempt[GeoPoint]:
    """Geocode a free-form UK address via Google Maps, ORS, then Nominatim."""
    cache_key = f"addr::{address.strip().upper()}"
    cache = _geo_cache(services=services)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    # ── 1: Google Maps Geocoding ──────────────────────────────────
    # Note: No pre-check for the API key. Just try the call — the mock
    # transport handles it in tests, and in production a missing key
    # produces a non-OK response that falls through to the next geocoder.
    result = await _geocode_google(address, cache_key, services=services)
    if result is not None:
        return result

    # ── 2: ORS Pelias ─────────────────────────────────────────────
    # Note: No pre-check for the API key (same reason as Google Maps).
    # The exhausted flag is kept to stop hammering a rate-limited API.
    result = await _geocode_ors(address, cache_key, services=services)
    if result is not None:
        return result

    # ── 3: Nominatim (free, no key, works for UK) ─────────────────
    logger.info("Falling back to Nominatim for '%s'", address)
    return await _geocode_nominatim(address, services=services)


async def _geocode_postcode(postcode: str, *, services: Any | None = None) -> Attempt[GeoPoint]:
    """Geocode a UK postcode via postcodes.io with in-memory caching."""
    key = postcode.strip().upper()
    if not key:
        return Attempt.impossible("empty postcode")
    cache = _geo_cache(services=services)
    cached = cache.get(key)
    if cached is not None:
        return cached

    is_outcode = bool(_OUTCODE_RE.match(key))
    url = f"{OUTCODES_IO_URL}/{key}" if is_outcode else f"{POSTCODES_IO_URL}/{key}"
    disk = get_cached("GET", url, None, None)
    if disk is not None:
        data = disk
        result = data.get("result")
        if not result:
            return Attempt.impossible("postcode not found")
        gp = GeoPoint(result["latitude"], result["longitude"])
        attempt = Attempt.succeeded(gp)
        _cache_result(key, attempt, services=services)
        return attempt
    else:
        try:
            async with cached_async_client(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", url, None, None, data)
                result = data.get("result")
                if not result:
                    return Attempt.impossible("postcode not found")
                gp = GeoPoint(result["latitude"], result["longitude"])
                attempt = Attempt.succeeded(gp)
                _cache_result(key, attempt, services=services)
                return attempt
        except httpx.HTTPStatusError as e:
            if e.response.status_code == HTTP_NOT_FOUND:
                _cache_result(key, Attempt.impossible("postcode not found (404)"), services=services)
                set_cached("GET", url, None, None, {})
                return Attempt.impossible("postcode not found (404)")
            logger.warning("Geocode HTTP error for %s: %s", key, e)
            return Attempt.impossible(f"HTTP {e.response.status_code}")
        # lucidlint: ignore broad-except deliberate broad catch — boundary/fallback per coding-standards.md
        except Exception:
            logger.exception("Failed to geocode postcode: %s", key)
            return Attempt.impossible("unexpected error")


async def find_nearest_town_name(
    lat: float,
    lon: float,
    *,
    options: ReverseGeocodeOptions | None = None,
) -> Attempt[str]:
    """Reverse-geocode coordinates to the nearest UK town name via ORS Pelias.

    Returns ``Attempt.succeeded(town)`` with the locality or borough name,
    or ``Attempt.impossible(reason)`` when the API fails or no result is
    found — the reason is preserved so the caller can distinguish "no
    town here" from "API down".

    ``options`` carries the API key and the cache/client test seams, all
    defaulting to the module implementations, so tests never monkeypatch
    houses.location globals.
    """
    options = options or ReverseGeocodeOptions()
    rev_url = ORS_GEOCODE_URL.replace("/search", "/reverse")
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    params = {"point.lat": lat, "point.lon": lon, "size": 1, "boundary.country": "GBR"}
    headers = {}
    api_key = options.api_key
    if api_key is None:
        api_key = settings.ors_api_key
    if api_key:
        headers["Authorization"] = api_key

    get_cached_fn = options.get_cached_fn or get_cached
    set_cached_fn = options.set_cached_fn or set_cached
    client_factory = options.client_factory or cached_async_client

    cached = get_cached_fn("GET", rev_url, params, None)
    if cached is not None:
        data = cached
    else:
        try:
            async with client_factory(timeout=10.0) as client:
                resp = await client.get(rev_url, params=params, headers=headers or None)
                resp.raise_for_status()
                data = resp.json()
                set_cached_fn("GET", rev_url, params, None, data)
        except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException):
            raise  # transient — let DAG retry handle it
        # lucidlint: ignore broad-except boundary — reverse-geocode failures convert to an impossible attempt
        except Exception as e:
            return Attempt.impossible(f"reverse geocode failed: {e}")

    features = data.get("features", [])
    if not features:
        return Attempt.impossible("no town found for coordinates")
    props = features[0].get("properties", {})
    town = props.get("locality") or props.get("borough")
    if not town:
        return Attempt.impossible("no town name in geocode response")
    return Attempt.succeeded(town)


# ── Postcode helpers ────────────────────────────────────────────────────


# Full postcode regex (e.g. "SW20 9NB") — uses word boundaries so it doesn't
# match longer strings that happen to contain a postcode pattern.
_FULL_POSTCODE_RE = re.compile(
    r"[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}",
    re.IGNORECASE,
)
# Outcode-within-address regex (e.g. "SW20" inside a longer address string) —
# uses word boundaries to avoid matching partial words.
_ADDR_OUTCODE_RE = re.compile(
    r"\b[A-Z]{1,2}[0-9][A-Z0-9]?\b",
    re.IGNORECASE,
)


def is_outcode(s: str) -> bool:
    """True if the string is a partial postcode (outcode) like 'SL6' or 'SW1E'."""
    return bool(re.match(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$", s))


def extract_postcode(address: str) -> str:
    """Extract the best postcode from an address string.

    Tries full postcode first (e.g. "SL6 1AA"), then falls back to
    outcode only (e.g. "SL6"). Returns empty string if nothing found.
    """
    m = _FULL_POSTCODE_RE.search(address)
    if m:
        return m.group(0).strip().upper()
    m = _ADDR_OUTCODE_RE.search(address)
    if m:
        return m.group(0).strip().upper()
    return ""


# ── House location resolution ───────────────────────────────────────────


async def resolve_house_location(
    inputs: HouseLocationInputs,
) -> GeoPoint | None:
    """Resolve the property's physical location, best source first.

    Priority:
    1. **Address + full postcode** — geocode the combined string (most precise
       when a full postcode like ``"SW20 9NB"`` is available, as opposed to
       an outcode like ``"SW20"`` which maps to a large area centroid).
    2. **Best lat/lon from the data sheet** — ``actual_latitude/longitude``
       (user-provided site measurement) if available, then ``approx_lat/lng``
       from a prior geocoding step.
    3. **Address only** — geocode just the street address without a postcode
       (least precise, but better than nothing).
    """
    coords: GeoPoint | None = None

    # 1. Address + full postcode
    if inputs.postcode and not is_outcode(inputs.postcode) and inputs.address:
        loc = PropertyLocation(postcode=inputs.postcode, address=inputs.address)
        loc = await loc.resolve()
        coords = loc.coordinates.value_or_none()

    # 2. Best lat/lon from the data sheet
    if coords is None and inputs.actual_latitude is not None and inputs.actual_longitude is not None:
        coords = GeoPoint(inputs.actual_latitude, inputs.actual_longitude)
    if coords is None and inputs.approx_lat is not None and inputs.approx_lng is not None:
        coords = GeoPoint(inputs.approx_lat, inputs.approx_lng)

    # 3. Address only
    if coords is None and inputs.address:
        loc = PropertyLocation(postcode="", address=inputs.address)
        loc = await loc.resolve()
        coords = loc.coordinates.value_or_none()

    return coords


@dataclass(frozen=True)
class WalkabilityFns:
    """Function seams for walkability enrichment — tests inject fakes here
    (docs/testing-standards.md — no monkeypatching); None means the real
    API-backed implementation.
    """

    extract_town_centre: Callable | None = None
    walk_duration: Callable | None = None
    reverse_geocode: Callable | None = None
    nearby_amenities: Callable | None = None

