"""PropertyLocation — where a property is on the map, possibly unresolved."""

from __future__ import annotations

import asyncio
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

# ── Geocoding API state ──────────────────────────────────────────


class _GeoState:
    ors_geo_exhausted: bool = False
    nominatim_exhausted: bool = False
    nominatim_last_call: float = 0.0


_geo_state = _GeoState()

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

# ── In-memory geocode cache ──────────────────────────────────────

_geo_cache: dict[str, GeoPoint | None] = {}


@dataclass(frozen=True)
class PropertyLocation:
    """Where a property is on the map, possibly unresolved.

    Create with a postcode and/or address, then ``await resolve()``
    to get coordinates.
    """

    postcode: str = ""
    address: str = ""
    coordinates: Attempt[GeoPoint] = Attempt.pending()

    async def resolve(self) -> PropertyLocation:
        """Resolve address first, then postcode.

        Only makes API calls when coordinates are still pending.
        Returns a new ``PropertyLocation`` with ``coordinates`` populated.
        """
        if not self.coordinates.is_pending:
            return self
        result = await _geocode_address(self.address)
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


async def _geocode_nominatim(query: str) -> Attempt[GeoPoint]:
    """Geocode a place name via Nominatim (free, 1 req/sec max)."""
    if _geo_state.nominatim_exhausted:
        return Attempt.impossible("nominatim", "rate limit exhausted")
    cache_key = f"nom::{query.strip().upper()}"
    if cache_key in _geo_cache:
        cached = _geo_cache[cache_key]
        if cached is not None:
            return Attempt.succeeded(cached, "nominatim")
        return Attempt.impossible("nominatim", "not found (cached)")
    clean = _END_PC_RE.sub("", query).strip()
    now = asyncio.get_event_loop().time()
    since_last = now - _geo_state.nominatim_last_call
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
            _geo_cache[cache_key] = gp
            return Attempt.succeeded(gp, "nominatim")
        return Attempt.impossible("nominatim", "no results")
    else:
        try:
            async with cached_async_client(timeout=10.0) as client:
                resp = await client.get(
                    NOMINATIM_URL,
                    params=params,
                    headers={"User-Agent": "HousesApp/1.0"},
                )
                _geo_state.nominatim_last_call = asyncio.get_event_loop().time()
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", NOMINATIM_URL, params, None, data)
                if data:
                    lat = float(data[0]["lat"])
                    lng = float(data[0]["lon"])
                    gp = GeoPoint(lat, lng)
                    _geo_cache[cache_key] = gp
                    return Attempt.succeeded(gp, "nominatim")
                return Attempt.impossible("nominatim", "no results")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                _geo_state.nominatim_exhausted = True
            logger.warning("Nominatim geocoding failed for %s (%s)", query, exc.response.status_code)
            return Attempt.impossible("nominatim", f"HTTP {exc.response.status_code}", exc)
        except Exception as exc:
            logger.warning("Nominatim geocoding failed for: %s", query)
            return Attempt.impossible("nominatim", "unexpected error", exc)


async def _geocode_address(address: str) -> Attempt[GeoPoint]:
    """Geocode a free-form UK address via Google Maps, ORS, then Nominatim."""
    cache_key = f"addr::{address.strip().upper()}"
    if cache_key in _geo_cache:
        cached = _geo_cache[cache_key]
        if cached is not None:
            return Attempt.succeeded(cached, "geocode_address")
        return Attempt.impossible("geocode_address", "not found (cached)")

    # Try Google Maps Geocoding first (best accuracy for UK streets)
    google_key = settings.google_maps_api_key
    if google_key:
        googlegeocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": f"{address}, UK", "key": google_key}
        cached = get_cached("GET", googlegeocode_url, params, None)
        if cached is not None:
            data = cached
            if data.get("status") == "OK" and data.get("results"):
                loc = data["results"][0]["geometry"]["location"]
                lat, lng = loc["lat"], loc["lng"]
                gp = GeoPoint(lat, lng)
                _geo_cache[cache_key] = gp
                return Attempt.succeeded(gp, "google-maps")
        else:
            try:
                async with cached_async_client(timeout=10.0) as client:
                    resp = await client.get(googlegeocode_url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    set_cached("GET", googlegeocode_url, params, None, data)
                    if data.get("status") == "OK" and data.get("results"):
                        loc = data["results"][0]["geometry"]["location"]
                        lat, lng = loc["lat"], loc["lng"]
                        gp = GeoPoint(lat, lng)
                        _geo_cache[cache_key] = gp
                        return Attempt.succeeded(gp, "google-maps")
            except Exception:
                logger.warning("Google Maps geocoding failed for %s", address)

    # Fallback to ORS Pelias (skip if exhausted)
    api_key = settings.ors_api_key
    if api_key and not _geo_state.ors_geo_exhausted:
        params = {"text": f"{address}, UK", "size": 1}
        cached = get_cached("GET", ORS_GEOCODE_URL, params, None)
        if cached is not None:
            data = cached
            features = data.get("features", [])
            if features:
                lng, lat = features[0]["geometry"]["coordinates"]
                gp = GeoPoint(lat, lng)
                _geo_cache[cache_key] = gp
                return Attempt.succeeded(gp, "ors-pelias")
        else:
            try:
                async with cached_async_client(timeout=10.0) as client:
                    resp = await retry_async(
                        lambda: client.get(
                            ORS_GEOCODE_URL,
                            params=params,
                            headers={"Authorization": api_key},
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
                        _geo_cache[cache_key] = gp
                        return Attempt.succeeded(gp, "ors-pelias")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 429):
                    _geo_state.ors_geo_exhausted = True
                logger.warning("ORS geocoding failed for %s (%s)", address, exc.response.status_code)
            except Exception:
                logger.warning("ORS geocoding failed for %s", address)

    # Final fallback: Nominatim (free, no key, works for UK)
    return await _geocode_nominatim(address)


async def _geocode_postcode(postcode: str) -> Attempt[GeoPoint]:
    """Geocode a UK postcode via postcodes.io with in-memory caching."""
    key = postcode.strip().upper()
    if not key:
        return Attempt.impossible("geocode_postcode", "empty postcode")
    if key in _geo_cache:
        cached = _geo_cache[key]
        if cached is not None:
            return Attempt.succeeded(cached, "geocode_postcode")
        return Attempt.impossible("geocode_postcode", "postcode not found (cached)")

    is_outcode = bool(_OUTCODE_RE.match(key))
    url = f"{OUTCODES_IO_URL}/{key}" if is_outcode else f"{POSTCODES_IO_URL}/{key}"
    cached = get_cached("GET", url, None, None)
    if cached is not None:
        data = cached
        result = data.get("result")
        if not result:
            return Attempt.impossible("postcodes.io", "postcode not found")
        gp = GeoPoint(result["latitude"], result["longitude"])
        _geo_cache[key] = gp
        return Attempt.succeeded(gp, "postcodes.io")
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
                _geo_cache[key] = gp
                return Attempt.succeeded(gp, "postcodes.io")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                _geo_cache[key] = None
                set_cached("GET", url, None, None, {})
                return Attempt.impossible("postcodes.io", "postcode not found (404)")
            logger.warning("Geocode HTTP error for %s: %s", key, e)
            return Attempt.impossible("postcodes.io", f"HTTP {e.response.status_code}", e)
        except Exception as exc:
            logger.exception("Failed to geocode postcode: %s", key)
            return Attempt.impossible("geocode_postcode", "unexpected error", exc)
