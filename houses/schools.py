"""School lookup — find the nearest suitable school from GIAS data.

Usage::

    school = await find_nearest(postcode, child_age=7, requirement=SchoolGender.BOYS)
    if school:
        commute = compute_school_commute(postcode, school)
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from houses.commute import Commute
from houses.config import settings
from houses.geo import GeoPoint
from houses.location import _geocode_address, geocode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gender — GIAS column value / query requirement
# ---------------------------------------------------------------------------


class SchoolGender(StrEnum):
    """GIAS 'Gender (name)' column values — also used as query requirements.

    SchoolGender.BOYS   → "I need a school for my boy(s)"
    SchoolGender.GIRLS  → "I need a school for my girl(s)"
    SchoolGender.MIXED  → "My children are a mix — I need a coeducational school"
    """
    BOYS = "boys"
    GIRLS = "girls"
    MIXED = "mixed"
    UNKNOWN = "unknown"  # GIAS "Not applicable" — treated as "accepts no one"


# ---------------------------------------------------------------------------
# School — a UK educational establishment from GIAS data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class School:
    """A UK educational establishment from GIAS data."""

    # Column name constants (private — only matter inside from_GIAS_row)
    _COL_NAME: ClassVar[str] = "EstablishmentName"
    _COL_PHASE: ClassVar[str] = "PhaseOfEducation (name)"
    _COL_GENDER: ClassVar[str] = "Gender (name)"
    _COL_TYPE: ClassVar[str] = "TypeOfEstablishment (name)"
    _COL_POSTCODE: ClassVar[str] = "Postcode"
    _COL_URN: ClassVar[str] = "URN"
    _COL_WEBSITE: ClassVar[str] = "SchoolWebsite"
    _COL_OFSTED: ClassVar[str] = "OfstedRating (name)"
    _COL_INSPECTION_YEAR: ClassVar[str] = "InspectionYear"
    _COL_LAT: ClassVar[str] = "Latitude"
    _COL_LNG: ClassVar[str] = "Longitude"
    _COL_LOW_AGE: ClassVar[str] = "StatutoryLowAge"
    _COL_HIGH_AGE: ClassVar[str] = "StatutoryHighAge"

    _FEE_PAYING_TYPES: ClassVar[frozenset] = frozenset({
        "independent school",
        "other independent school",
        "independent special school",
        "non-maintained special school",
    })

    # ── Inherent school properties ──────────────────────────────────
    urn: str
    name: str
    phase: str  # raw PhaseOfEducation value from GIAS (e.g. "Primary", "Secondary")
    gender: SchoolGender
    type_of_establishment: str
    postcode: str
    website: str
    ofsted_rating: str
    inspection_year: str
    coords: GeoPoint | None

    # Age range from GIAS (fallback when phase-based ranges don't apply)
    statutory_low_age: int | None
    statutory_high_age: int | None

    # ── Age ranges by phase ─────────────────────────────────────────
    # PhaseOfEducation is clean (controlled vocabulary, 21k+ schools). The
    # statutory age ranges are unreliable for some schools (e.g. secondary
    # schools reporting age 0). Use well-known UK age bands per phase.
    _PHASE_RANGES: ClassVar[dict[str, tuple[int, int]]] = {
        "nursery": (2, 4),
        "primary": (4, 11),
        "middle deemed primary": (9, 13),
        "middle deemed secondary": (9, 14),
        "secondary": (11, 18),
        "16 plus": (16, 18),
        "all-through": (4, 18),
    }

    # ── Derived properties ──────────────────────────────────────────

    @property
    def fee_paying(self) -> bool:
        return self.type_of_establishment.lower() in self._FEE_PAYING_TYPES

    # ── Factory ─────────────────────────────────────────────────────

    @staticmethod
    def _try_int(raw: str) -> int | None:
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

    @classmethod
    def from_GIAS_row(cls, row: dict) -> School:  # noqa: N802 — GIAS is an acronym
        lat = row.get(cls._COL_LAT)
        lng = row.get(cls._COL_LNG)
        raw_gender = (row.get(cls._COL_GENDER) or "").strip().lower()
        try:
            gender = SchoolGender(raw_gender)
        except ValueError:
            gender = SchoolGender.UNKNOWN
        return cls(
            urn=(row.get(cls._COL_URN) or "").strip(),
            name=(row.get(cls._COL_NAME) or "").strip(),
            phase=(row.get(cls._COL_PHASE) or "").strip(),
            statutory_low_age=cls._try_int(row.get(cls._COL_LOW_AGE)),
            statutory_high_age=cls._try_int(row.get(cls._COL_HIGH_AGE)),
            gender=gender,
            type_of_establishment=(row.get(cls._COL_TYPE) or "").strip(),
            postcode=(row.get(cls._COL_POSTCODE) or "").strip(),
            website=(row.get(cls._COL_WEBSITE) or "").strip(),
            ofsted_rating=(row.get(cls._COL_OFSTED) or "").strip(),
            inspection_year=(row.get(cls._COL_INSPECTION_YEAR) or "").strip(),
            coords=GeoPoint(float(lat), float(lng)) if lat and lng else None,
        )

    # ── Queries ─────────────────────────────────────────────────────

    def accepts(self, requirement: SchoolGender) -> bool:
        """Can this school satisfy the given requirement?

            SchoolGender.BOYS   → school must accept boys   (BOYS or MIXED)
            SchoolGender.GIRLS  → school must accept girls  (GIRLS or MIXED)
            SchoolGender.MIXED  → school must be coeducational (MIXED only)
            SchoolGender.UNKNOWN  → always False (can't verify)
        """
        if self.gender == SchoolGender.UNKNOWN:
            return False
        return self.gender in (SchoolGender.MIXED, requirement)

    def accepts_age(self, child_age: int) -> bool:
        """Can a child of this age attend this school?

        Uses the phase-controlled vocabulary as primary filter (covers 93% of
        schools). Falls back to statutory age ranges for "Not applicable" and
        unknown phases.
        """
        phase_key = self.phase.lower()
        if phase_key in self._PHASE_RANGES:
            low, high = self._PHASE_RANGES[phase_key]
            return low <= child_age <= high
        # Fallback: "Not applicable" special schools, PRUs, etc.
        too_young = self.statutory_low_age is not None and child_age < self.statutory_low_age
        too_old = self.statutory_high_age is not None and child_age > self.statutory_high_age
        return not too_young and not too_old


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
    requirement: SchoolGender,
) -> School | None:
    """Find the nearest school accepting a child of the given age and gender.

    Args:
        postcode: Property postcode (used to geocode and compute distances).
        child_age: Age of the child (checked against school's age range).
        address: Property address (fallback if postcode geocoding fails).
        requirement: Gender requirement (boys, girls, or mixed).

    Returns the nearest ``School`` or ``None`` if no suitable school is found
    within the configured search radius.
    """
    schools = _load_schools()
    if not schools:
        return None

    property_coords = (await geocode(postcode)).value_or_none()
    if property_coords is None and address:
        property_coords = (await _geocode_address(address)).value_or_none()
    if property_coords is None:
        return None

    candidates: list[tuple[float, School]] = []

    for school in schools:
        if not school.accepts(requirement):
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
    from houses.routing import get_commute

    result = await get_commute(property_postcode, school.postcode, has_car=False, max_walk_minutes=20)
    if result.is_impossible:
        import logging

        logging.getLogger(__name__).debug(
            "School commute for %s → %s: %s", property_postcode, school.postcode, result.reason
        )
    return result.value_or_none()
