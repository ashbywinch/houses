"""Transit commute, petrol cost, and school lookup logic.

Uses TravelTime API for transit routing, OpenRouteService for
driving distances, and UK government school data for education lookups.
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

POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TRAVELTIME_URL = "https://api.traveltimeapp.com/v4/time-filter"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"


def _build_traveltime_headers() -> dict[str, str]:
    return {
        "X-Application-Id": settings.traveltime_app_id,
        "X-Api-Key": settings.traveltime_api_key,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Transit — TravelTime API
# ---------------------------------------------------------------------------

async def compute_transit(
    origin_postcode: str,
    destination_postcode: str,
    label: str,
) -> TransitInfo:
    """Return transit commute time from origin to destination (minutes).

    Uses TravelTime API with public_transport mode.
    Returns a TransitInfo with duration_minutes=None on failure or
    if API is not configured.
    """
    if not settings.traveltime_app_id or not settings.traveltime_api_key:
        logger.warning("TravelTime API not configured; skipping transit for %s", label)
        return TransitInfo(
            destination_label=label,
            destination_postcode=destination_postcode,
            duration_minutes=None,
            mode="transit",
        )

    payload = {
        "locations": [
            {"id": "origin", "postcode": origin_postcode},
            {"id": "destination", "postcode": destination_postcode},
        ],
        "departure_searches": [
            {
                "id": "commute",
                "coords": {"lat": 51.5, "lon": -0.13},  # placeholder — overridden by postcode
                "transportation": {"type": "public_transport"},
                "departure_time": "2026-06-02T08:00:00",
                "travel_time": 7200,
                "properties": ["travel_time"],
                "range": {"enabled": False},
            }
        ],
    }
    # TravelTime v4 requires origin/destination as proper location references.
    # The postcode-based search is done via locations[].postcode and then
    # arrival_searches / departure_searches reference location IDs.
    # Re-structure for postcode input:
    payload = {
        "locations": [
            {"id": "origin", "postcode": origin_postcode},
            {"id": "destination", "postcode": destination_postcode},
        ],
        "departure_searches": [
            {
                "id": "to-work",
                "departure_location_id": "origin",
                "arrival_location_ids": ["destination"],
                "transportation": {"type": "public_transport"},
                "departure_time": "2026-06-02T08:00:00",
                "travel_time": 5400,
                "properties": ["travel_time"],
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await retry_async(
                lambda: client.post(
                    TRAVELTIME_URL,
                    headers=_build_traveltime_headers(),
                    json=payload,
                ),
                max_retries=2,
                base_delay=1.0,
                exceptions=(httpx.HTTPStatusError, httpx.RequestError),
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        if results:
            travel_time_secs = results[0].get("travel_time", 0)
            duration_minutes = round(travel_time_secs / 60)
        else:
            duration_minutes = None
    except httpx.HTTPStatusError as e:
        logger.error("TravelTime API HTTP error for %s: %s", label, e)
        duration_minutes = None
    except httpx.RequestError as e:
        logger.error("TravelTime API request failed for %s: %s", label, e)
        duration_minutes = None
    except (KeyError, IndexError, TypeError) as e:
        logger.error("TravelTime API unexpected response for %s: %s", label, e)
        duration_minutes = None

    return TransitInfo(
        destination_label=label,
        destination_postcode=destination_postcode,
        duration_minutes=duration_minutes,
        mode="transit",
    )


async def compute_simon_commute(property_postcode: str) -> TransitInfo:
    """Transit time from property to Simon's work anchor (Pimlico/Victoria)."""
    return await compute_transit(
        property_postcode,
        settings.simon_postcode,
        "Simon — Pimlico / Victoria",
    )


async def compute_lorena_commute(property_postcode: str) -> TransitInfo:
    """Transit time from property to Lorena's work anchor (Aldgate)."""
    return await compute_transit(
        property_postcode,
        settings.lorena_postcode,
        "Lorena — Aldgate / City of London",
    )


# ---------------------------------------------------------------------------
# Petrol — OpenRouteService driving distance
# ---------------------------------------------------------------------------

def _compute_petrol_from_distance_km(round_trip_km: float) -> float:
    """Convert driving distance to petrol cost.

    45 mpg → 5.23 L/100km → cost = (km / 100) * 5.23 * price_per_litre
    """
    litres_per_100km = 235.214 / settings.petrol_mpg  # ~5.23 L/100km at 45mpg
    litres_used = (round_trip_km / 100) * litres_per_100km
    return round(litres_used * settings.petrol_price_per_litre, 2)


async def compute_petrol_cost(origin_postcode: str) -> PetrolCost:
    """Estimate round-trip petrol cost to the Bracknell office.

    Uses OpenRouteService driving-car profile to get one-way distance,
    doubles it for round trip, then applies 45 mpg at £1.45/L cost calc.
    """
    if not settings.ors_api_key:
        logger.warning("ORS API key not configured; skipping petrol cost")
        return PetrolCost()

    # We need to geocode the postcodes first. ORS doesn't accept
    # postcodes directly for directions — it needs coordinates.
    # Use a simple approach: geocode via ORS Pelias search.
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
                logger.warning("Could not geocode origin postcode: %s", origin_postcode)
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
                logger.warning("Could not geocode Bracknell postcode")
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
        logger.error("Failed to compute petrol cost for %s: %s", origin_postcode, e)
        return PetrolCost()


# ---------------------------------------------------------------------------
# Schools — UK government GIAS / Get Information About Schools
# ---------------------------------------------------------------------------
# There is no official public REST API for GIAS that provides free,
# anonymous proximity search. The recommended approach is:
#
#   Option A — Bulk data download (recommended for a local server):
#     Download the full GIAS CSV from
#     https://www.get-information-schools.service.gov.uk/Downloads
#     This is ~25 MB. Load locally, filter by postcode proximity.
#
#   Option B — Ofsted API (requires API key, limited free tier).
#
# We implement Option A here since it's free, offline once downloaded,
# and gives full control over the gender/fee-substitution logic.

SCHOOLS_CSV_PATH = Path("data/uk_schools.csv")


async def _postcode_to_lat_lng(postcode: str) -> tuple[float, float] | None:
    """Look up lat/lng for a UK postcode using postcodes.io (free, no key)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await retry_async(
                lambda: client.get(f"{POSTCODES_IO_URL}/{postcode}"),
                max_retries=2, base_delay=0.5,
                exceptions=(httpx.HTTPStatusError, httpx.RequestError),
            )
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result")
            if not result:
                return None
            return result["latitude"], result["longitude"]
    except Exception:
        logger.exception("Failed to geocode postcode: %s", postcode)
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance between two lat/lng points in km."""
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
    """Load schools from local CSV. Returns empty list if file missing."""
    if not SCHOOLS_CSV_PATH.is_file():
        logger.warning("Schools CSV not found at %s", SCHOOLS_CSV_PATH)
        return []
    with SCHOOLS_CSV_PATH.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


FEE_PAYING_TYPES = frozenset({
    "independent school",
    "other independent school",
    "independent special school",
    "non-maintained special school",
})


def _boys_eligible(school: dict) -> bool:
    gender = (school.get("Gender") or "").strip().lower()
    if gender not in ("mixed", "boys"):
        return False
    estab_type = (school.get("TypeOfEstablishment") or "").strip().lower()
    return estab_type not in FEE_PAYING_TYPES


def _phase_filter(school: dict, target: str) -> bool:
    phase = (school.get("PhaseOfEducation") or "").strip().lower()
    return target in phase


def _school_to_info(school: dict, dist_km: float, school_type: str) -> SchoolInfo:
    return SchoolInfo(
        name=school.get("EstablishmentName", "Unknown"),
        type=school_type,
        distance_km=round(dist_km, 2),
        gender=(school.get("Gender") or "").strip().lower(),
        fee_paying=False,
        walking_time_minutes=None,
    )


async def _find_nearest_boys(
    postcode: str,
    target_phase: str,
) -> SchoolInfo | None:
    schools = _load_schools()
    if not schools:
        return None

    coords = await _postcode_to_lat_lng(postcode)
    if coords is None:
        return None

    origin_lat, origin_lon = coords
    candidates: list[tuple[float, dict]] = []

    for school in schools:
        if not _phase_filter(school, target_phase):
            continue
        if not _boys_eligible(school):
            continue
        try:
            slat = float(school.get("Latitude", 0))
            slon = float(school.get("Longitude", 0))
        except (ValueError, TypeError):
            continue
        if not slat or not slon:
            continue
        dist = _haversine_km(origin_lat, origin_lon, slat, slon)
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
