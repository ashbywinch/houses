"""PropertyLocation — where a property is on the map, possibly unresolved."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import re
from dataclasses import dataclass, replace

import httpx

from houses.api_cache import cached_async_client, get_cached, set_cached, with_cache
from houses.attempt import Attempt
from houses.config import settings
from houses.geo import GeoPoint
from houses.retry import retry_async

logger = logging.getLogger(__name__)

# ── Geocoding API state (per-request via contextvars) ─────────────


class _GeoState:
    ors_geo_exhausted: bool = False
    nominatim_exhausted: bool = False
    nominatim_last_call: float = 0.0


_GEO_STATE_SENTINEL: _GeoState = _GeoState()

_geo_state_var: contextvars.ContextVar[_GeoState] = contextvars.ContextVar("geo_state", default=_GEO_STATE_SENTINEL)


def _get_geo_state() -> _GeoState:
    state = _geo_state_var.get()
    if state is _GEO_STATE_SENTINEL:
        state = _GeoState()
        _geo_state_var.set(state)
    return state


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

# ── In-memory geocode cache (per-request via contextvars) ─────────

_geo_cache_var: contextvars.ContextVar[dict[str, Attempt[GeoPoint]]] = contextvars.ContextVar("geo_cache", default=None)  # type: ignore[arg-type]


def _cache_result(key: str, result: Attempt[GeoPoint]) -> None:
    """Store a geocode result in the per-request cache."""
    cache = _geo_cache_var.get()
    if cache is None:
        cache = {}
        _geo_cache_var.set(cache)
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

    async def resolve(self) -> PropertyLocation:
        """Resolve address first, then postcode.

        When the address ends with an outcode but we have a full postcode,
        the address is upgraded to include the full postcode before geocoding
        (see ``_upgrade_address``).  This prevents ambiguous street names
        (e.g. ``"Grand Drive, London, SW20"`` — there are many Grand Drives
        across UK outcodes) from returning wrong coordinates.

        Only makes API calls when coordinates are still pending.
        Returns a new ``PropertyLocation`` with ``coordinates`` populated.
        """
        if not self.coordinates.is_pending:
            return self

        address = self._upgrade_address(self.address, self.postcode)
        result = await _geocode_address(address)
        if result.is_succeeded:
            return replace(self, coordinates=result)
        result = await _geocode_postcode(self.postcode)
        return replace(self, coordinates=result)

    def resolved(self, point: GeoPoint, source: str) -> PropertyLocation:
        """Return a new PropertyLocation with coordinates pre-set from a known value.

        Useful when the sheet already has coordinates and re-geocoding is unnecessary.
        """
        return replace(self, coordinates=Attempt.succeeded(point, source))

    @classmethod
    async def from_town(cls, town: str) -> PropertyLocation:
        """Resolve a town name to a PropertyLocation.

        Tries ORS Pelias, then Nominatim. Strips common suffixes
        (``Station Area``, ``Village``, etc.) as a final fallback.
        """
        key = town.strip().upper()
        if not key:
            return cls(coordinates=Attempt.impossible("from_town", "empty town name"))

        # ORS Pelias — disk-cached via with_cache
        ors_params = {"text": f"{town}, UK", "size": 1}
        try:
            async with cached_async_client(timeout=10.0) as client:

                async def _fetch_ors():
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

                data = await with_cache("GET", ORS_GEOCODE_URL, params=ors_params, fetch=_fetch_ors)
                features = data.get("features", [])
                if features:
                    lng, lat = features[0]["geometry"]["coordinates"]
                    return cls(coordinates=Attempt.succeeded(GeoPoint(lat, lng), "ors"))
        except httpx.HTTPStatusError as exc:
            logger.warning("ORS geocoding failed for town: %s (%s)", town, exc.response.status_code)
        except Exception:
            logger.warning("ORS geocoding failed for town: %s", town)

        # Fallback: Nominatim (free, no key) — disk-cached via with_cache
        nom_params = {"q": f"{town}, UK", "format": "json", "limit": 1}
        try:
            async with cached_async_client(timeout=10.0) as client:

                async def _fetch_nom():
                    resp = await client.get(
                        "https://nominatim.openstreetmap.org/search",
                        params=nom_params,
                        headers={"User-Agent": "HousesApp/1.0"},
                    )
                    resp.raise_for_status()
                    return resp.json()

                nom_url = "https://nominatim.openstreetmap.org/search"
                data = await with_cache("GET", nom_url, params=nom_params, fetch=_fetch_nom)
                if data:
                    return cls(
                        coordinates=Attempt.succeeded(
                            GeoPoint(float(data[0]["lat"]), float(data[0]["lon"])), "nominatim"
                        )
                    )
        except httpx.HTTPStatusError as exc:
            logger.warning("Nominatim geocoding failed for town: %s (%s)", town, exc.response.status_code)
        except Exception:
            logger.warning("Nominatim geocoding failed for town: %s", town)

        # Try stripping suffixes like "Station Area" → "Maidenhead"
        stripped = _TOWN_SUFFIXES.sub("", town).strip()
        if stripped and stripped.upper() != key:
            return await cls.from_town(stripped)

        return cls(coordinates=Attempt.impossible("from_town", "all geocoders failed"))


# ── Private helpers ──────────────────────────────────────────────


async def geocode(postcode: str) -> Attempt[GeoPoint]:
    """Geocode a UK postcode — public entry point for one-shot lookups."""
    return await _geocode_postcode(postcode)


async def _geocode_nominatim(query: str) -> Attempt[GeoPoint]:
    """Geocode a place name via Nominatim (free, 1 req/sec max)."""
    if _get_geo_state().nominatim_exhausted:
        return Attempt.impossible("nominatim", "rate limit exhausted")
    cache_key = f"nom::{query.strip().upper()}"
    cache = _geo_cache_var.get()
    if cache is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    clean = _END_PC_RE.sub("", query).strip()
    now = asyncio.get_event_loop().time()
    since_last = now - _get_geo_state().nominatim_last_call
    if since_last < 1.0:
        await asyncio.sleep(1.0 - since_last)
    params = {"q": f"{clean}, UK", "format": "json", "limit": 1}
    cached = get_cached("GET", NOMINATIM_URL, params, None)
    if cached is not None:
        data = cached
        if data:
            lat = float(data[0]["lat"])
            lng = float(data[0]["lon"])
            gp = GeoPoint(lat, lng)
            result = Attempt.succeeded(gp, "nominatim")
            _cache_result(cache_key, result)
            return result
        return Attempt.impossible("nominatim", "no results")
    else:
        try:
            async with cached_async_client(timeout=10.0) as client:
                resp = await client.get(
                    NOMINATIM_URL,
                    params=params,
                    headers={"User-Agent": "HousesApp/1.0"},
                )
                _get_geo_state().nominatim_last_call = asyncio.get_event_loop().time()
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", NOMINATIM_URL, params, None, data)
                if data:
                    lat = float(data[0]["lat"])
                    lng = float(data[0]["lon"])
                    gp = GeoPoint(lat, lng)
                    result = Attempt.succeeded(gp, "nominatim")
                    _cache_result(cache_key, result)
                    return result
                return Attempt.impossible("nominatim", "no results")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                _get_geo_state().nominatim_exhausted = True
            logger.warning("Nominatim geocoding failed for %s (%s)", query, exc.response.status_code)
            return Attempt.impossible("nominatim", f"HTTP {exc.response.status_code}", exc)
        except Exception as exc:
            logger.warning("Nominatim geocoding failed for: %s", query)
            return Attempt.impossible("nominatim", "unexpected error", exc)


async def _geocode_address(address: str) -> Attempt[GeoPoint]:
    """Geocode a free-form UK address via Google Maps, ORS, then Nominatim."""
    cache_key = f"addr::{address.strip().upper()}"
    cache = _geo_cache_var.get()
    if cache is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    # ── 1: Google Maps Geocoding ──────────────────────────────────
    # Note: No pre-check for the API key. Just try the call — the mock
    # transport handles it in tests, and in production a missing key
    # produces a non-OK response that falls through to the next geocoder.
    googlegeocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": f"{address}, UK", "key": settings.google_maps_api_key}
    cache_params = {"address": f"{address}, UK"}
    cached = get_cached("GET", googlegeocode_url, cache_params, None)
    if cached is not None:
        data = cached
        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            gp = GeoPoint(loc["lat"], loc["lng"])
            result = Attempt.succeeded(gp, "google-maps")
            _cache_result(cache_key, result)
            logger.info("Geocoded '%s' via google-maps (cached)", address)
            return result
        logger.warning(
            "Google Maps cached result for '%s' rejected: status=%s msg=%s",
            address,
            data.get("status"),
            data.get("error_message", ""),
        )
    else:
        try:
            async with cached_async_client(timeout=10.0) as client:
                resp = await client.get(googlegeocode_url, params=params)
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "OK" and data.get("results"):
                    set_cached("GET", googlegeocode_url, cache_params, None, data)
                    loc = data["results"][0]["geometry"]["location"]
                    gp = GeoPoint(loc["lat"], loc["lng"])
                    result = Attempt.succeeded(gp, "google-maps")
                    _cache_result(cache_key, result)
                    logger.info("Geocoded '%s' via google-maps", address)
                    return result
                logger.warning(
                    "Google Maps API response for '%s': status=%s msg=%s",
                    address,
                    data.get("status"),
                    data.get("error_message", ""),
                )
        except Exception as exc:
            logger.warning("Google Maps geocoding failed for '%s': %s", address, exc)

    # ── 2: ORS Pelias ─────────────────────────────────────────────
    # Note: No pre-check for the API key (same reason as Google Maps).
    # The exhausted flag is kept to stop hammering a rate-limited API.
    if not _get_geo_state().ors_geo_exhausted:
        params = {"text": f"{address}, UK", "size": 1}
        cached = get_cached("GET", ORS_GEOCODE_URL, params, None)
        if cached is not None:
            data = cached
            features = data.get("features", [])
            if features:
                lng, lat = features[0]["geometry"]["coordinates"]
                gp = GeoPoint(lat, lng)
                result = Attempt.succeeded(gp, "ors-pelias")
                _cache_result(cache_key, result)
                logger.info("Geocoded '%s' via ors-pelias (cached) → (%s, %s)", address, f"{lat:.4f}", f"{lng:.4f}")
                return result
        else:
            try:
                async with cached_async_client(timeout=10.0) as client:
                    resp = await retry_async(
                        lambda: client.get(
                            ORS_GEOCODE_URL,
                            params=params,
                            headers={"Authorization": settings.ors_api_key},
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
                        gp = GeoPoint(lat, lng)
                        result = Attempt.succeeded(gp, "ors-pelias")
                        _cache_result(cache_key, result)
                        logger.info("Geocoded '%s' via ors-pelias → (%s, %s)", address, f"{lat:.4f}", f"{lng:.4f}")
                        return result
                    logger.warning("ORS returned no features for '%s'", address)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 429):
                    _get_geo_state().ors_geo_exhausted = True
                logger.warning("ORS geocoding failed for '%s': HTTP %s", address, exc.response.status_code)
            except Exception as exc:
                logger.warning("ORS geocoding failed for '%s': %s", address, exc)

    # ── 3: Nominatim (free, no key, works for UK) ─────────────────
    logger.info("Falling back to Nominatim for '%s'", address)
    return await _geocode_nominatim(address)


async def _geocode_postcode(postcode: str) -> Attempt[GeoPoint]:
    """Geocode a UK postcode via postcodes.io with in-memory caching."""
    key = postcode.strip().upper()
    if not key:
        return Attempt.impossible("geocode_postcode", "empty postcode")
    cache = _geo_cache_var.get()
    if cache is not None:
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
            return Attempt.impossible("postcodes.io", "postcode not found")
        gp = GeoPoint(result["latitude"], result["longitude"])
        attempt = Attempt.succeeded(gp, "postcodes.io")
        _cache_result(key, attempt)
        return attempt
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
                    return Attempt.impossible("postcodes.io", "postcode not found")
                gp = GeoPoint(result["latitude"], result["longitude"])
                attempt = Attempt.succeeded(gp, "postcodes.io")
                _cache_result(key, attempt)
                return attempt
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                _cache_result(key, Attempt.impossible("postcodes.io", "postcode not found (404)"))
                set_cached("GET", url, None, None, {})
                return Attempt.impossible("postcodes.io", "postcode not found (404)")
            logger.warning("Geocode HTTP error for %s: %s", key, e)
            return Attempt.impossible("postcodes.io", f"HTTP {e.response.status_code}", e)
        except Exception as exc:
            logger.exception("Failed to geocode postcode: %s", key)
            return Attempt.impossible("geocode_postcode", "unexpected error", exc)
