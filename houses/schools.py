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
from dag.attempt import Attempt
from houses.config import settings
from houses.geo import GeoPoint
from houses.location import _geocode_address, geocode
from houses.model.domain import Commute
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
) -> Attempt[School | None]:
    """Find the nearest school accepting a child of the given age.

    For each school matching age/gender/fee/name filters:
    - If the school has reliable (corrected building-level) coords,
      distance is computed and it is added to the candidate list.
    - If the school has only a postcode centroid (``_postcode_centroid``),
      the centroid is used to check proximity: if within the search
      radius the school is noted as a nearby candidate without reliable
      coords (``skipped_no_coords``).
    - If the school has neither (no location data at all), it is
      silently skipped.

    Returns ``Attempt.succeeded(school)`` when a nearest school is found,
    ``Attempt.pending()`` when no school with reliable coords is within
    range but some nearby schools lack reliable coords (the answer is
    inconclusive — retry when coordinate data improves), and
    ``Attempt.succeeded(None)`` when no school matches at all
    (no candidates at any quality level).
    """
    from dag.attempt import Attempt as _Attempt

    schools = _load_schools()
    if not schools:
        return _Attempt.succeeded(None)

    property_coords = None
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
        return _Attempt.succeeded(None)
    candidates: list[tuple[float, School]] = []
    skipped_no_coords = False

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
        if sc is not None:
            dist = property_coords.distance_km_to(sc)
            if dist <= settings.school_search_radius_km:
                candidates.append((dist, school))
        elif school._postcode_centroid is not None:
            # No reliable coords but we have a postcode centroid —
            # check if this school is roughly nearby.
            dist = property_coords.distance_km_to(school._postcode_centroid)
            if dist <= settings.school_search_radius_km:
                skipped_no_coords = True
    if not candidates:
        if skipped_no_coords:
            return _Attempt.pending()
        return _Attempt.succeeded(None)

    candidates.sort(key=lambda x: x[0])
    return _Attempt.succeeded(candidates[0][1])


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
        logger.debug("School commute for %s → %s: %s", property_postcode, school.postcode, result.error)
    return result.value_or_none()
