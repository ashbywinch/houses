"""School lookup — find the nearest suitable school from GIAS data.

Usage::

    school = await find_nearest(postcode, child_age=7, requirement=SchoolGender.BOYS)
    if school:
        commute = compute_school_commute(postcode, school)
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import houses.routing as _routing
from houses.commute import Commute
from houses.config import settings
from houses.geo import GeoPoint
from houses.location import _geocode_address, geocode
from houses.school import School
from houses.school_gender import SchoolGender

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


SCHOOLS_CSV_PATH = Path("data/edubaseall_enriched.csv")


def _load_schools() -> list[School]:
    if not SCHOOLS_CSV_PATH.is_file():
        logger.warning("Schools CSV not found at %s", SCHOOLS_CSV_PATH)
        return []
    with SCHOOLS_CSV_PATH.open(newline="", encoding="latin-1") as f:
        return [School.from_GIAS_row(row) for row in csv.DictReader(f)]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def find_nearest(
    postcode: str,
    child_age: int,
    address: str = "",
    *,
    acceptable: tuple[SchoolGender, ...] = (SchoolGender.MIXED,),
) -> School | None:
    """Find the nearest school accepting a child of the given age.

    Args:
        postcode: Property postcode (used to geocode and compute distances).
        child_age: Age of the child (checked against school's age range).
        address: Property address (fallback if postcode geocoding fails).
        acceptable: Tuple of SchoolGender values the family finds acceptable.

    Returns the nearest ``School`` or ``None`` if no suitable school is found
    within the configured search radius.
    """
    schools = _load_schools()
    if not schools:
        return None

    property_coords = None
    # If the input is a "lat,lon" coordinate string, parse it directly
    # instead of trying to geocode it (geocoding would fail).
    if "," in postcode:
        try:
            lat_str, lon_str = postcode.split(",", 1)
            property_coords = GeoPoint(lat=float(lat_str), lon=float(lon_str))
        except (ValueError, TypeError):
            pass

    if property_coords is None:
        property_coords = (await geocode(postcode)).value_or_none()
    if property_coords is None and address:
        property_coords = (await _geocode_address(address)).value_or_none()
    if property_coords is None:
        return None

    candidates: list[tuple[float, School]] = []

    for school in schools:
        if not school.accepts_any(acceptable):
            continue
        if not school.accepts_age(child_age):
            continue
        if school.fee_paying:
            continue
        if not school.name.strip():
            continue
        sc = school.coords
        if sc is None:
            school_postcode = school.postcode
            if not school_postcode:
                continue
            sc = (await geocode(school_postcode)).value_or_none()
            if sc is None:
                continue
        dist = property_coords.distance_km_to(sc)
        if dist <= settings.school_search_radius_km:
            candidates.append((dist, school))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]



# ---------------------------------------------------------------------------
# School commute
# ---------------------------------------------------------------------------


async def compute_school_commute(property_postcode: str, school: School) -> Commute | None:
    """Compute the commute from a property to a school.

    Delegates to ``get_commute(has_car=False, max_walk_minutes=20)``.
    Returns ``None`` silently — the caller's sheet formatting handles
    missing commutes.
    """
    result = await _routing.get_commute(property_postcode, school.postcode, has_car=False, max_walk_minutes=20)
    if result.impossible:
        logger.debug(
            "School commute for %s → %s: %s", property_postcode, school.postcode, result.error
        )
    return result.value_or_none()
