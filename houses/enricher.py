"""Transit commute, petrol cost, and school lookup logic.

Uses TfL Unified API for transit routing, OpenRouteService for
driving distances, and UK government GIAS school data.
"""

from __future__ import annotations

import csv
import logging
import math
from pathlib import Path

import httpx

from houses.config import settings
from houses.models import PetrolCost, SchoolInfo, TransitInfo
from houses.retry import retry_async

logger = logging.getLogger(__name__)

POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"
TFL_JOURNEY_URL = "https://api.tfl.gov.uk/Journey/JourneyResults"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"

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

        journeys = data.get("journeys", [])
        duration_minutes = journeys[0].get("duration") if journeys else None
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning("TfL could not route %s: route may be outside London area", label)
        else:
            logger.error("TfL API HTTP error for %s: %s", label, e)
        duration_minutes = None
    except httpx.RequestError as e:
        logger.error("TfL API request failed for %s: %s", label, e)
        duration_minutes = None
    except (KeyError, IndexError, TypeError) as e:
        logger.error("TfL API unexpected response for %s: %s", label, e)
        duration_minutes = None

    return TransitInfo(
        destination_label=label,
        destination_postcode=destination_postcode,
        duration_minutes=duration_minutes,
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

    geocode_url = "https://api.openrouteservice.org/geocode/search"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            geo_resp = await retry_async(
                lambda: client.get(
                    geocode_url,
                    params={"text": f"{origin_postcode}, UK", "size": 1},
                    headers={"Authorization": settings.ors_api_key},
                ),
                max_retries=2,
                base_delay=1.0,
                exceptions=(httpx.HTTPStatusError, httpx.RequestError),
            )
            geo_resp.raise_for_status()
            geo_data = geo_resp.json()
            features = geo_data.get("features", [])
            if not features:
                logger.warning("Could not geocode origin: %s", origin_postcode)
                return PetrolCost()
            origin_coords = features[0]["geometry"]["coordinates"]

            geo_resp2 = await retry_async(
                lambda: client.get(
                    geocode_url,
                    params={"text": f"{settings.bracknell_postcode}, UK", "size": 1},
                    headers={"Authorization": settings.ors_api_key},
                ),
                max_retries=2,
                base_delay=1.0,
                exceptions=(httpx.HTTPStatusError, httpx.RequestError),
            )
            geo_resp2.raise_for_status()
            dest_data = geo_resp2.json()
            dest_features = dest_data.get("features", [])
            if not dest_features:
                logger.warning("Could not geocode Bracknell")
                return PetrolCost()
            dest_coords = dest_features[0]["geometry"]["coordinates"]

        async with httpx.AsyncClient(timeout=15.0) as client:
            dir_resp = await retry_async(
                lambda: client.post(
                    ORS_DIRECTIONS_URL,
                    headers={
                        "Authorization": settings.ors_api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "coordinates": [origin_coords, dest_coords],
                        "units": "km",
                    },
                ),
                max_retries=2,
                base_delay=1.0,
                exceptions=(httpx.HTTPStatusError, httpx.RequestError),
            )
            dir_resp.raise_for_status()
            dir_data = dir_resp.json()

        one_way_km = dir_data["routes"][0]["summary"]["distance"]
        round_trip_km = round(one_way_km * 2, 1)
        cost = _compute_petrol_from_distance_km(round_trip_km)

        return PetrolCost(round_trip_km=round_trip_km, cost_gbp=cost)

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

# The enriched CSV — has Latitude/Longitude columns added via scripts/enrich_schools.py
# Falls back to postcodes.io on-the-fly for any schools missing coordinates.
SCHOOLS_CSV_PATH = Path("data/edubaseall_enriched.csv")

FEE_PAYING_TYPES = frozenset({
    "independent school",
    "other independent school",
    "independent special school",
    "non-maintained special school",
})

# In-memory cache: postcode -> (lat, lng)
_geo_cache: dict[str, tuple[float, float]] = {}


async def _geocode(postcode: str) -> tuple[float, float] | None:
    """Geocode a UK postcode via postcodes.io with in-memory caching."""
    key = postcode.strip().upper()
    if key in _geo_cache:
        return _geo_cache[key]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await retry_async(
                lambda: client.get(f"{POSTCODES_IO_URL}/{key}"),
                max_retries=2, base_delay=0.5,
                exceptions=(httpx.HTTPStatusError, httpx.RequestError),
            )
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result")
            if not result:
                return None
            latlng = result["latitude"], result["longitude"]
            _geo_cache[key] = latlng
            return latlng
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            _geo_cache[key] = None  # don't retry invalid postcodes
        else:
            logger.warning("Geocode HTTP error for %s: %s", key, e)
        return None
    except Exception:
        logger.exception("Failed to geocode postcode: %s", key)
        return None
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
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
    return SchoolInfo(
        name=school.get(COL_NAME, "Unknown"),
        type=school_type,
        distance_km=round(dist_km, 2),
        gender=(school.get(COL_GENDER) or "").strip().lower(),
        fee_paying=False,
        walking_time_minutes=None,
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
) -> SchoolInfo | None:
    schools = _load_schools()
    if not schools:
        return None

    property_coords = await _geocode(postcode)
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
    return _school_to_info(best, candidates[0][0], target_phase)


async def find_nearest_boys_primary(postcode: str) -> SchoolInfo | None:
    return await _find_nearest_boys(postcode, "primary")


async def find_nearest_boys_secondary(postcode: str) -> SchoolInfo | None:
    return await _find_nearest_boys(postcode, "secondary")
