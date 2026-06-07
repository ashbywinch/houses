"""Transit commute, petrol cost, and school lookup logic.

Uses TfL Unified API for transit routing, OpenRouteService for
driving distances, and UK government GIAS school data.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import logging
import math
import re
from pathlib import Path

import httpx

from houses.api_cache import get_cached, set_cached
from houses.config import settings
from houses.models import CommuteBreakdown, PetrolCost, SchoolInfo, TransitInfo
from houses.retry import retry_async

logger = logging.getLogger(__name__)


# Per-process-run API exhaustion tracking.
# Set when an API returns a usage-limit error so subsequent calls
# skip straight to the fallback instead of hammering the dead endpoint.
class _APIState:
    places_exhausted: bool = False
    ors_geo_exhausted: bool = False
    nominatim_exhausted: bool = False
    nominatim_last_call: float = 0.0


_api_state = _APIState()

POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"
OUTCODES_IO_URL = "https://api.postcodes.io/outcodes"
TFL_JOURNEY_URL = "https://api.tfl.gov.uk/Journey/JourneyResults"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"

# ---------------------------------------------------------------------------
# API response cache helpers
# ---------------------------------------------------------------------------


async def _cached_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    *,
    max_retries: int = 0,
) -> dict | None:
    """GET with disk-backed JSON caching. Returns the parsed JSON or ``None``."""
    cached = get_cached("GET", url, params)
    if cached is not None:
        return cached

    async def _fetch():
        return await client.get(url, params=params)

    resp = await retry_async(_fetch, max_retries=max_retries, base_delay=0.5) if max_retries else await _fetch()
    resp.raise_for_status()
    data = resp.json()
    set_cached("GET", url, params, None, data)
    return data


async def _cached_post(
    client: httpx.AsyncClient,
    url: str,
    json_body: dict | None = None,
    headers: dict | None = None,
    *,
    max_retries: int = 0,
) -> dict | None:
    """POST with disk-backed JSON caching. Returns the parsed JSON or ``None``."""
    body_str = json.dumps(json_body, sort_keys=True) if json_body else None
    cached = get_cached("POST", url, None, body_str)
    if cached is not None:
        return cached

    async def _fetch():
        return await client.post(url, json=json_body, headers=headers)

    resp = await retry_async(_fetch, max_retries=max_retries, base_delay=0.5) if max_retries else await _fetch()
    resp.raise_for_status()
    data = resp.json()
    set_cached("POST", url, None, body_str, data)
    return data


# Full postcode: "SL6 1AA", outcode: "SL6"
_OUTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")
# Trailing postcode in address strings (e.g. ", SL6" or ", GU22 8BQ")
_END_PC_RE = re.compile(r",\s*[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?\s*$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Transit — TfL Unified API (free, no expiring trial)
# ---------------------------------------------------------------------------


def _tfl_auth_params() -> dict[str, str]:
    params = {}
    if settings.tfl_api_key:
        params["app_key"] = settings.tfl_api_key
    return params


async def compute_transit(
    origin_postcode: str,
    destination_postcode: str,
    label: str,
) -> TransitInfo:
    """Return transit commute time using TfL Unified API (free, London focus).

    Returns TransitInfo with duration_minutes=None if API unavailable or
    if the route is outside TfL's coverage area.
    """
    if not settings.tfl_api_key:
        logger.warning("TfL API key not configured; skipping transit for %s", label)
        return TransitInfo(
            destination_label=label,
            destination_postcode=destination_postcode,
            duration_minutes=None,
            mode="transit",
        )

    url = f"{TFL_JOURNEY_URL}/{origin_postcode}/to/{destination_postcode}"
    params = {
        "nationalSearch": "true",
        "timeIs": "departing",
        "journeyPreference": "leasttime",
        "mode": "tube,bus,overground,dlr,tram,national-rail,walking",
        **_tfl_auth_params(),
    }

    duration_minutes = None
    daily_cost_gbp = None
    data = None

    # Check cache first
    cached = get_cached("GET", url, params)
    if cached is not None:
        data = cached
        journeys = data.get("journeys", [])
        duration_minutes = journeys[0].get("duration") if journeys else None
        if journeys:
            fare = journeys[0].get("fare")
            if fare and fare.get("totalCost") is not None:
                daily_cost_gbp = round(fare["totalCost"] / 100.0 * 2, 2)
    else:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await retry_async(
                    lambda: client.get(url, params=params),
                    max_retries=2,
                    base_delay=1.0,
                    exceptions=(httpx.HTTPStatusError, httpx.RequestError),
                )
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", url, params, None, data)

            journeys = data.get("journeys", [])
            duration_minutes = journeys[0].get("duration") if journeys else None
            if journeys:
                fare = journeys[0].get("fare")
                if fare and fare.get("totalCost") is not None:
                    daily_cost_gbp = round(fare["totalCost"] / 100.0 * 2, 2)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("TfL could not route %s: route may be outside London area", label)
            elif e.response.status_code == 300:
                pc_match = re.search(r"[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?", origin_postcode)
                pc = pc_match.group(0).strip().upper() if pc_match else None
                coords = await _geocode(pc) if pc else None
                if coords is None:
                    coords = await _geocode_address(origin_postcode)
                if coords:
                    latlng = f"{coords[0]},{coords[1]}"
                    url2 = f"{TFL_JOURNEY_URL}/{latlng}/to/{destination_postcode}"
                    try:
                        async with httpx.AsyncClient(timeout=20.0) as c2:
                            r2 = await c2.get(url2, params=params)
                            r2.raise_for_status()
                            d2 = r2.json()
                            set_cached("GET", url2, params, None, d2)
                            j2 = d2.get("journeys", [])
                            if j2:
                                duration_minutes = j2[0].get("duration")
                                f2 = j2[0].get("fare")
                                if f2 and f2.get("totalCost") is not None:
                                    daily_cost_gbp = round(f2["totalCost"] / 100.0 * 2, 2)
                    except Exception:
                        logger.warning("TfL geocode fallback failed for %s", label)
            else:
                logger.error("TfL API HTTP error for %s: %s", label, e)
        except httpx.RequestError as e:
            logger.error("TfL API request failed for %s: %s", label, e)
        except (KeyError, IndexError, TypeError) as e:
            logger.error("TfL API unexpected response for %s: %s", label, e)

    return TransitInfo(
        destination_label=label,
        destination_postcode=destination_postcode,
        duration_minutes=duration_minutes,
        daily_cost_gbp=daily_cost_gbp,
        mode="transit",
    )


async def compute_simon_commute(property_postcode: str) -> TransitInfo:
    return await compute_transit(
        property_postcode,
        settings.simon_postcode,
        "Simon — Pimlico / Victoria",
    )


async def compute_lorena_commute(property_postcode: str) -> TransitInfo:
    return await compute_transit(
        property_postcode,
        settings.lorena_postcode,
        "Lorena — Aldgate / City of London",
    )


async def compute_commute_breakdown(
    simon_transit: TransitInfo,
    lorena_transit: TransitInfo,
    petrol: PetrolCost,
) -> CommuteBreakdown:
    simon_daily = simon_transit.daily_cost_gbp
    lorena_daily = lorena_transit.daily_cost_gbp
    bracknell_daily = petrol.cost_gbp

    yearly_total = None
    formula = ""

    if simon_daily is not None and lorena_daily is not None and bracknell_daily is not None:
        yearly_total = settings.working_weeks_per_year * (
            bracknell_daily + simon_daily + lorena_daily * settings.weekly_lorena_trips
        )
        yearly_total = round(yearly_total, 2)
        formula = (
            f"{settings.working_weeks_per_year}wk x "
            f"({settings.weekly_bracknell_trips}xBracknell_daily + "
            f"{settings.weekly_lorena_trips}xLorena_daily + "
            f"{settings.weekly_simon_trips}xSimon_daily)"
        )

    return CommuteBreakdown(
        simon_daily_gbp=simon_daily,
        lorena_daily_gbp=lorena_daily,
        bracknell_daily_gbp=bracknell_daily,
        yearly_total_gbp=yearly_total,
        formula_explanation=formula,
    )


# ---------------------------------------------------------------------------
# Petrol — OpenRouteService driving distance
# ---------------------------------------------------------------------------


def _compute_petrol_from_distance_km(round_trip_km: float) -> float:
    litres_per_100km = 235.214 / settings.petrol_mpg
    litres_used = (round_trip_km / 100) * litres_per_100km
    return round(litres_used * settings.petrol_price_per_litre, 2)


async def compute_petrol_cost(origin_postcode: str) -> PetrolCost:
    if not settings.ors_api_key:
        logger.warning("ORS API key not configured; skipping petrol cost")
        return PetrolCost()

    try:
        # Use postcodes.io first (more reliable for UK), fall back to ORS
        origin_coords = None
        coords = await _geocode(origin_postcode)
        if coords is None:
            coords = await _geocode_address(origin_postcode)
        if coords is not None:
            # _geocode returns (lat, lng), ORS needs [lng, lat]
            origin_coords = [coords[1], coords[0]]

        if origin_coords is None:
            logger.warning("Could not geocode origin: %s", origin_postcode)
            return PetrolCost()

        # Geocode Bracknell via postcodes.io (more reliable), fall back to ORS
        bracknell_coords = await _geocode(settings.bracknell_postcode)
        if bracknell_coords is None:
            logger.warning("Could not geocode Bracknell")
            return PetrolCost()
        # _geocode returns (lat, lng), ORS needs [lng, lat]
        dest_coords = [bracknell_coords[1], bracknell_coords[0]]

        async with httpx.AsyncClient(timeout=15.0) as client:
            body = {"coordinates": [origin_coords, dest_coords], "units": "km"}
            cached = get_cached("POST", ORS_DIRECTIONS_URL, None, json.dumps(body, sort_keys=True))
            if cached is not None:
                dir_data = cached
            else:
                dir_resp = await retry_async(
                    lambda: client.post(
                        ORS_DIRECTIONS_URL,
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
                dir_resp.raise_for_status()
                dir_data = dir_resp.json()
                set_cached("POST", ORS_DIRECTIONS_URL, None, json.dumps(body, sort_keys=True), dir_data)

        one_way_km = dir_data["routes"][0]["summary"]["distance"]
        one_way_duration_sec = dir_data["routes"][0]["summary"]["duration"]
        round_trip_km = round(one_way_km * 2, 1)
        round_trip_minutes = round(one_way_duration_sec * 2 / 60)
        cost = _compute_petrol_from_distance_km(round_trip_km)

        return PetrolCost(round_trip_km=round_trip_km, round_trip_minutes=round_trip_minutes, cost_gbp=cost)

    except (httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as e:
        logger.error("Petrol cost failed for %s: %s", origin_postcode, e)
        return PetrolCost()


# ---------------------------------------------------------------------------
# Schools — GIAS CSV + postcodes.io geocoding
# ---------------------------------------------------------------------------

# GIAS column name mappings — the CSVs use "FieldName (name)" format
COL_NAME = "EstablishmentName"
COL_PHASE = "PhaseOfEducation (name)"
COL_GENDER = "Gender (name)"
COL_TYPE = "TypeOfEstablishment (name)"
COL_POSTCODE = "Postcode"
COL_URN = "URN"
COL_WEBSITE = "SchoolWebsite"
COL_OFSTED = "OfstedRating (name)"
COL_INSPECTION_YEAR = "InspectionYear"

# The enriched CSV — has Latitude/Longitude columns added via scripts/enrich_schools.py
# Falls back to postcodes.io on-the-fly for any schools missing coordinates.
SCHOOLS_CSV_PATH = Path("data/edubaseall_enriched.csv")

FEE_PAYING_TYPES = frozenset(
    {
        "independent school",
        "other independent school",
        "independent special school",
        "non-maintained special school",
    }
)

# In-memory cache: postcode -> (lat, lng)
_geo_cache: dict[str, tuple[float, float]] = {}
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


async def _geocode_nominatim(query: str) -> tuple[float, float] | None:
    """Geocode a place name via Nominatim (free, 1 req/sec max).

    Respects Nominatim's usage policy by enforcing at least 1 second
    between consecutive calls. If still rate-limited, sets exhaustion
    flag and returns None.
    """
    if _api_state.nominatim_exhausted:
        return None
    cache_key = f"nom::{query.strip().upper()}"
    if cache_key in _geo_cache:
        return _geo_cache[cache_key]
    # Strip trailing postcode so Nominatim doesn't choke on ", SL6" etc.
    clean = _END_PC_RE.sub("", query).strip()
    # Enforce 1 req/sec rate limit
    now = asyncio.get_event_loop().time()
    since_last = now - _api_state.nominatim_last_call
    if since_last < 1.0:
        await asyncio.sleep(1.0 - since_last)
    params = {"q": f"{clean}, UK", "format": "json", "limit": 1}
    cached = get_cached("GET", NOMINATIM_URL, params, None)
    if cached is not None:
        data = cached
        if data:
            lat = float(data[0]["lat"])
            lng = float(data[0]["lon"])
            _geo_cache[cache_key] = (lat, lng)
            return (lat, lng)
    else:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    NOMINATIM_URL,
                    params=params,
                    headers={"User-Agent": "HousesApp/1.0"},
                )
                _api_state.nominatim_last_call = asyncio.get_event_loop().time()
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", NOMINATIM_URL, params, None, data)
                if data:
                    lat = float(data[0]["lat"])
                    lng = float(data[0]["lon"])
                    _geo_cache[cache_key] = (lat, lng)
                    return (lat, lng)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                _api_state.nominatim_exhausted = True
            logger.warning("Nominatim geocoding failed for %s (%s)", query, exc.response.status_code)
        except Exception:
            logger.warning("Nominatim geocoding failed for: %s", query)
    return None


async def _geocode_address(address: str) -> tuple[float, float] | None:
    """Geocode a free-form UK address.

    Tries Google Maps, ORS Pelias, then Nominatim as final fallback.
    Used when postcodes.io can't resolve an outcode.
    """
    cache_key = f"addr::{address.strip().upper()}"
    if cache_key in _geo_cache:
        return _geo_cache[cache_key]

    # Try Google Maps Geocoding first (best accuracy for UK streets)
    google_key = settings.google_maps_api_key
    if google_key:
        google_geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": f"{address}, UK", "key": google_key}
        cached = get_cached("GET", google_geocode_url, params, None)
        if cached is not None:
            data = cached
            if data.get("status") == "OK" and data.get("results"):
                loc = data["results"][0]["geometry"]["location"]
                lat, lng = loc["lat"], loc["lng"]
                _geo_cache[cache_key] = (lat, lng)
                return (lat, lng)
        else:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        google_geocode_url,
                        params=params,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    set_cached("GET", google_geocode_url, params, None, data)
                    if data.get("status") == "OK" and data.get("results"):
                        loc = data["results"][0]["geometry"]["location"]
                        lat, lng = loc["lat"], loc["lng"]
                        _geo_cache[cache_key] = (lat, lng)
                        return (lat, lng)
            except Exception:
                logger.warning("Google Maps geocoding failed for %s", address)

    # Fallback to ORS Pelias (skip if exhausted to avoid hammering)
    api_key = settings.ors_api_key
    if api_key and not _api_state.ors_geo_exhausted:
        params = {"text": f"{address}, UK", "size": 1}
        cached = get_cached("GET", ORS_GEOCODE_URL, params, None)
        if cached is not None:
            data = cached
            features = data.get("features", [])
            if features:
                lng, lat = features[0]["geometry"]["coordinates"]
                _geo_cache[cache_key] = (lat, lng)
                return (lat, lng)
        else:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
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
                        _geo_cache[cache_key] = (lat, lng)
                        return (lat, lng)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 429):
                    _api_state.ors_geo_exhausted = True
                logger.warning("ORS geocoding failed for %s (%s)", address, exc.response.status_code)
            except Exception:
                logger.warning("ORS geocoding failed for %s", address)

    # Final fallback: Nominatim (free, no key, works for UK)
    result = await _geocode_nominatim(address)
    if result:
        _geo_cache[cache_key] = result
    return result


async def _geocode(postcode: str) -> tuple[float, float] | None:
    """Geocode a UK postcode via postcodes.io with in-memory caching.

    Supports both full postcodes ("SL6 1AA") and outcodes ("SL6").
    """
    key = postcode.strip().upper()
    if not key:
        return None
    if key in _geo_cache:
        return _geo_cache[key]

    is_outcode = bool(_OUTCODE_RE.match(key))

    url = f"{OUTCODES_IO_URL}/{key}" if is_outcode else f"{POSTCODES_IO_URL}/{key}"
    cached = get_cached("GET", url, None, None)
    if cached is not None:
        data = cached
        result = data.get("result")
        if not result:
            return None
        latlng = result["latitude"], result["longitude"]
        _geo_cache[key] = latlng
        return latlng
    else:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
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
                    return None
                latlng = result["latitude"], result["longitude"]
                _geo_cache[key] = latlng
                return latlng
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                _geo_cache[key] = None
                set_cached("GET", url, None, None, {})  # cache empty dict for 404
            else:
                logger.warning("Geocode HTTP error for %s: %s", key, e)
            return None
        except Exception:
            logger.exception("Failed to geocode postcode: %s", key)
            return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _load_schools() -> list[dict]:
    if not SCHOOLS_CSV_PATH.is_file():
        logger.warning("Schools CSV not found at %s", SCHOOLS_CSV_PATH)
        return []
    with SCHOOLS_CSV_PATH.open(newline="", encoding="latin-1") as f:
        return list(csv.DictReader(f))


def _boys_eligible(school: dict) -> bool:
    gender = (school.get(COL_GENDER) or "").strip().lower()
    if gender not in ("mixed", "boys"):
        return False
    estab_type = (school.get(COL_TYPE) or "").strip().lower()
    return estab_type not in FEE_PAYING_TYPES


def _phase_filter(school: dict, target: str) -> bool:
    phase = (school.get(COL_PHASE) or "").strip().lower()
    return target in phase


def _school_to_info(school: dict, dist_km: float, school_type: str) -> SchoolInfo:
    walk_mins = round(dist_km / 5 * 60) if dist_km else None
    # Bus time only makes sense when walking is impractical (> 20 min)
    bus_mins = None
    return SchoolInfo(
        name=school.get(COL_NAME, "Unknown"),
        type=school_type,
        distance_km=round(dist_km, 2) if dist_km else None,
        gender=(school.get(COL_GENDER) or "").strip().lower(),
        fee_paying=False,
        walking_time_minutes=walk_mins,
        bus_time_minutes=bus_mins,
        urn=school.get(COL_URN, ""),
        website=school.get(COL_WEBSITE, ""),
        ofsted_rating=school.get(COL_OFSTED, ""),
        inspection_year=school.get(COL_INSPECTION_YEAR, ""),
    )


def _school_coords(school: dict) -> tuple[float, float] | None:
    """Read lat/lng from the enriched CSV row."""
    try:
        lat = school.get("Latitude")
        lng = school.get("Longitude")
        if lat and lng:
            return float(lat), float(lng)
    except (ValueError, TypeError):
        pass
    return None


async def _find_nearest_boys(
    postcode: str,
    target_phase: str,
    address: str = "",
) -> SchoolInfo | None:
    schools = _load_schools()
    if not schools:
        return None

    property_coords = await _geocode(postcode)
    if property_coords is None and address:
        property_coords = await _geocode_address(address)
    if property_coords is None:
        return None

    origin_lat, origin_lon = property_coords
    candidates: list[tuple[float, dict]] = []

    for school in schools:
        if not _phase_filter(school, target_phase):
            continue
        if not _boys_eligible(school):
            continue

        school_coords = _school_coords(school)
        if school_coords is None:
            school_postcode = school.get(COL_POSTCODE, "")
            if not school_postcode:
                continue
            school_coords = await _geocode(school_postcode)
            if school_coords is None:
                continue

        dist = _haversine_km(origin_lat, origin_lon, school_coords[0], school_coords[1])
        if dist <= settings.school_search_radius_km:
            candidates.append((dist, school))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    _, best = candidates[0]
    info = _school_to_info(best, candidates[0][0], target_phase)

    if (
        info.type == "secondary"
        and info.walking_time_minutes
        and info.walking_time_minutes > 20
        and settings.google_maps_api_key
    ):
        school_coords = _school_coords(best)
        if school_coords:
            routes_url = "https://routes.googleapis.com/directions/v2:computeRoutes"
            body = {
                "origin": {
                    "location": {"latLng": {"latitude": origin_lat, "longitude": origin_lon}},
                },
                "destination": {
                    "location": {"latLng": {"latitude": school_coords[0], "longitude": school_coords[1]}},
                },
                "travelMode": "TRANSIT",
            }
            cached = get_cached("POST", routes_url, None, json.dumps(body, sort_keys=True))
            if cached is not None:
                data = cached
                leg = data.get("routes", [{}])[0].get("legs", [{}])[0]
                duration_s = leg.get("duration", "")
                if duration_s and duration_s.endswith("s"):
                    with contextlib.suppress(ValueError):
                        info.bus_time_minutes = round(int(duration_s.rstrip("s")) / 60)
                steps = leg.get("steps", [])
                for s in steps:
                    td = s.get("transitDetails")
                    if td:
                        line = td.get("transitLine", {}).get("nameShort", "")
                        dep = td.get("stopDetails", {}).get("departureStop", {}).get("name", "")
                        if line and dep:
                            info.bus_route = f"{line} from {dep}"
                            break
            else:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as c:
                        resp = await c.post(
                            routes_url,
                            headers={
                                "X-Goog-Api-Key": settings.google_maps_api_key,
                                "X-Goog-FieldMask": "routes.legs.duration,routes.legs.steps.transitDetails",
                                "Content-Type": "application/json",
                            },
                            json=body,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            set_cached("POST", routes_url, None, json.dumps(body, sort_keys=True), data)
                            leg = data.get("routes", [{}])[0].get("legs", [{}])[0]
                            duration_s = leg.get("duration", "")
                            if duration_s and duration_s.endswith("s"):
                                with contextlib.suppress(ValueError):
                                    info.bus_time_minutes = round(int(duration_s.rstrip("s")) / 60)
                            steps = leg.get("steps", [])
                            for s in steps:
                                td = s.get("transitDetails")
                                if td:
                                    line = td.get("transitLine", {}).get("nameShort", "")
                                    dep = td.get("stopDetails", {}).get("departureStop", {}).get("name", "")
                                    if line and dep:
                                        info.bus_route = f"{line} from {dep}"
                                        break
                        else:
                            logger.error(
                                "Routes API returned %d — enable routes.googleapis.com",
                                resp.status_code,
                            )
                except Exception as e:
                    logger.error("Bus directions failed: %s", e)

    return info


async def find_nearest_boys_primary(postcode: str, address: str = "") -> SchoolInfo | None:
    return await _find_nearest_boys(postcode, "primary", address)


async def find_nearest_boys_secondary(postcode: str, address: str = "") -> SchoolInfo | None:
    return await _find_nearest_boys(postcode, "secondary", address)
