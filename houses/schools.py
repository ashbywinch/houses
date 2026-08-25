"""School lookup — find the nearest suitable school from GIAS data.

Usage::

    school = await find_nearest(postcode, child_age=7, requirement=SchoolGender.BOYS)
    if school:
        commute = compute_school_commute(postcode, school)
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dag.attempt import Attempt
from houses.commute_router import CommuteRouter
from houses.geopoint import GeoPoint
from houses.location import geocode, geocode_address
from houses.model.domain import Commute
from houses.school import School
from houses.school_gender import SchoolGender
from houses.settings import settings

logger = logging.getLogger(__name__)
# Module-level CommuteRouter instance for school commute lookups.
_router = CommuteRouter()


@dataclass(frozen=True)
class SchoolLookupOptions:
    """Lookup configuration for ``find_nearest``: gender filter plus the
    geocoder/school-source test seams (defaulting to the real modules)."""

    acceptable: tuple[SchoolGender, ...] = (SchoolGender.MIXED,)
    geocode_fn: Callable | None = None
    geocode_address_fn: Callable | None = None
    load_schools_fn: Callable | None = None




class CommuteRouterLike(Protocol):
    """Structural type for ``compute_school_commute``'s router seam —
    test fakes implement just ``get_commute``."""

    # lucidlint: ignore detached-method protocol stub mirrors CommuteRouter's instance method — typing matches by kind
    async def get_commute(
        self,
        origin: str | GeoPoint,
        dest: str | GeoPoint,
        *,
        has_car: bool,
        max_walk_minutes: int | None = None,
    ) -> Attempt[Commute]: ...

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


def _parse_coords(postcode: str) -> GeoPoint | None:
    """Parse a ``"lat,lon"`` string into a GeoPoint, or None if not parseable."""
    try:
        lat_str, lon_str = postcode.split(",", 1)
        return GeoPoint(lat=float(lat_str), lon=float(lon_str))
    except (ValueError, TypeError):
        return None


async def _locate_property(postcode: str, address: str, geocode_fn, geocode_address_fn):
    """Property coordinates from a "lat,lon" postcode, geocoding, then the
    address; the returned Attempt carries the geocoding failure reason."""
    property_coords = None
    if "," in postcode:
        property_coords = _parse_coords(postcode)
    if property_coords is None:
        geocode_attempt = await geocode_fn(postcode)
        if geocode_attempt.impossible:
            return Attempt.impossible(
                geocode_attempt.error or "geocoding failed",
                error_info=geocode_attempt.error_info,
            )
        property_coords = geocode_attempt.value_or_none()
    if property_coords is None and address:
        addr_attempt = await geocode_address_fn(address)
        if addr_attempt.impossible:
            return Attempt.impossible(
                addr_attempt.error or "address geocoding failed",
                error_info=addr_attempt.error_info,
            )
        property_coords = addr_attempt.value_or_none()
    return Attempt.succeeded(property_coords)


def _school_eligible(school, acceptable, child_age: int) -> bool:
    """Whether the school accepts the child and is a mainstream option."""
    if not school.accepts_any(acceptable):
        return False
    if not school.accepts_age(child_age):
        return False
    if school.fee_paying:
        return False
    # Special schools are not the family's mainstream option — a
    # community/other special school must never surface as the nearest
    # primary or secondary (it accepts both age bands, so without this
    # filter primary and secondary collapse to the same special school).
    if school.type_of_establishment.lower().endswith("special school"):
        return False
    return bool(school.name.strip())


def _school_candidates(schools, property_coords, acceptable, child_age: int):
    """(distance, school) pairs within the search radius, plus whether any
    nearby school lacked reliable coordinates.

    A school with reliable (corrected building-level) coords is a
    candidate; one with only a postcode centroid notes ``skipped_no_coords``
    when roughly nearby; one with neither is silently skipped.
    """
    candidates: list[tuple[float, School]] = []
    skipped_no_coords = False
    for school in schools:
        if not _school_eligible(school, acceptable, child_age):
            continue
        sc = school.coords
        if sc is not None:
            dist = property_coords.distance_km_to(sc)
            if dist <= settings.school_search_radius.magnitude:
                candidates.append((dist, school))
        elif school._postcode_centroid is not None:
            # No reliable coords but we have a postcode centroid —
            # check if this school is roughly nearby.
            dist = property_coords.distance_km_to(school._postcode_centroid)
            if dist <= settings.school_search_radius.magnitude:
                skipped_no_coords = True
    return candidates, skipped_no_coords



async def find_nearest(
    postcode: str,
    child_age: int,
    address: str = "",
    *,
    options: SchoolLookupOptions | None = None,
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

    ``options`` carries the acceptable-gender filter and the geocoder /
    school-source test seams (defaulting to the real implementations).
    """
    options = options or SchoolLookupOptions()
    geocode_fn = options.geocode_fn or geocode
    geocode_address_fn = options.geocode_address_fn or geocode_address
    load_schools_fn = options.load_schools_fn or _load_schools

    schools = load_schools_fn()
    if not schools:
        return Attempt.succeeded(None)

    coords_attempt = await _locate_property(postcode, address, geocode_fn, geocode_address_fn)
    if coords_attempt.impossible:
        return coords_attempt
    property_coords = coords_attempt.value_or_none()
    if property_coords is None:
        return Attempt.succeeded(None)

    candidates, skipped_no_coords = _school_candidates(schools, property_coords, options.acceptable, child_age)
    if not candidates:
        if skipped_no_coords:
            return Attempt.pending()
        return Attempt.succeeded(None)

    candidates.sort(key=lambda x: x[0])
    return Attempt.succeeded(candidates[0][1])


# ---------------------------------------------------------------------------
# School commute
# ---------------------------------------------------------------------------


async def compute_school_commute(
    property_postcode: str, school: School, *, router: CommuteRouterLike | None = None
) -> Commute | None:
    """Compute the commute from a property to a school.

    Delegates to ``get_commute(has_car=False, max_walk_minutes=20)``.
    Returns ``None`` silently — the caller's sheet formatting handles
    """
    router = router or _router
    result = await router.get_commute(property_postcode, school.postcode, has_car=False, max_walk_minutes=20)
    if result.impossible:
        logger.debug("School commute for %s → %s: %s", property_postcode, school.postcode, result.error)
    return result.value_or_none()
