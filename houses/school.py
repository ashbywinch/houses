from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from houses.geopoint import GeoPoint
from houses.school_gender import SchoolGender

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class School:
    """A UK educational establishment from GIAS data."""

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
    _COL_CORR_LAT: ClassVar[str] = "CorrectedLatitude"
    _COL_CORR_LNG: ClassVar[str] = "CorrectedLongitude"
    _COL_LOW_AGE: ClassVar[str] = "StatutoryLowAge"
    _COL_HIGH_AGE: ClassVar[str] = "StatutoryHighAge"

    _FEE_PAYING_TYPES: ClassVar[frozenset] = frozenset(
        {
            "independent school",
            "other independent school",
            "independent special school",
            "non-maintained special school",
        }
    )

    urn: str
    name: str
    phase: str
    gender: SchoolGender
    type_of_establishment: str
    postcode: str
    website: str
    ofsted_rating: str
    inspection_year: str
    coords: GeoPoint | None
    statutory_low_age: int | None
    statutory_high_age: int | None
    full_address: str = ""
    url: str = ""
    # Postcode centroid from GIAS — approximate location for distance
    # gating when corrected (building-level) coords are unavailable.
    _postcode_centroid: GeoPoint | None = None
    _PHASE_RANGES: ClassVar[dict[str, tuple[int, int]]] = {
        "nursery": (2, 4),
        "primary": (4, 11),
        "middle deemed primary": (9, 13),
        "middle deemed secondary": (9, 14),
        "secondary": (11, 18),
        "16 plus": (16, 18),
        "all-through": (4, 18),
    }

    @property
    def fee_paying(self) -> bool:
        return self.type_of_establishment.lower() in self._FEE_PAYING_TYPES

    @staticmethod
    def _try_int(raw: Any) -> int | None:
        """Parse a raw GIAS row value as an int; None when absent/malformed."""
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

    @classmethod
    def from_GIAS_row(cls, row: dict) -> School:  # noqa: N802  # from_GIAS_row deliberately mirrors the GIAS acronym from the source dataset
        """Parse a GIAS CSV row into a School.

        ``coords`` is set to the corrected (building-level) coordinates
        when available and within 100km of the raw GIAS coordinates.
        Falls back to ``None`` — postcode centroids from the raw
        GIAS columns are not reliable enough for nearest-school
        distance calculations.
        """
        original: GeoPoint | None = None
        lat = row.get(cls._COL_LAT)
        lng = row.get(cls._COL_LNG)
        if lat and lng:
            with contextlib.suppress(ValueError, TypeError):
                original = GeoPoint(float(lat), float(lng))

        corr_lat = (row.get(cls._COL_CORR_LAT) or "").strip()
        corr_lng = (row.get(cls._COL_CORR_LNG) or "").strip()

        coords: GeoPoint | None = None
        if corr_lat and corr_lng:
            corrected = _try_geopoint(corr_lat, corr_lng)
            if corrected is not None and original is not None:
                if original.distance_km_to(corrected) < 100:
                    coords = corrected
            elif corrected is not None:
                coords = corrected
        raw_gender = (row.get(cls._COL_GENDER) or "").strip().lower()
        gender = _parse_gender(raw_gender)
        urn = (row.get(cls._COL_URN) or "").strip()
        # Assemble a full postal address from the GIAS columns — this is
        # the display destination for walk/drive legs, captured when the
        # school is first loaded from the data (not geocoded later).
        street = (row.get("Street") or "").strip()
        locality = (row.get("Locality") or "").strip()
        address3 = (row.get("Address3") or "").strip()
        town = (row.get("Town") or "").strip()
        county = (row.get("County (name)") or "").strip()
        postcode = (row.get(cls._COL_POSTCODE) or "").strip()
        address_parts = [p for p in (street, locality, address3, town, county, postcode) if p]
        full_address = ", ".join(address_parts)
        raw_website = (row.get(cls._COL_WEBSITE) or "").strip()
        website = raw_website
        # GIAS stores bare domains ("www.school.sch.uk") — the detail
        # page links to the school's own site, so the url needs a scheme.
        url = f"https://{raw_website}" if raw_website and "://" not in raw_website else raw_website
        return cls(
            urn=urn,
            name=(row.get(cls._COL_NAME) or "").strip(),
            phase=(row.get(cls._COL_PHASE) or "").strip(),
            statutory_low_age=cls._try_int(row.get(cls._COL_LOW_AGE)),
            statutory_high_age=cls._try_int(row.get(cls._COL_HIGH_AGE)),
            gender=gender,
            type_of_establishment=(row.get(cls._COL_TYPE) or "").strip(),
            postcode=postcode,
            website=website,
            ofsted_rating=(row.get(cls._COL_OFSTED) or "").strip(),
            inspection_year=(row.get(cls._COL_INSPECTION_YEAR) or "").strip(),
            coords=coords,
            full_address=full_address,
            url=url,
            _postcode_centroid=original,
        )

    def accepts_any(self, acceptable: tuple[SchoolGender, ...]) -> bool:
        """Check if this school's gender is in the acceptable set."""
        if self.gender == SchoolGender.UNKNOWN:
            return False
        return self.gender in acceptable

    def accepts(self, requirement: SchoolGender) -> bool:
        """Check if this school accepts a child of the given gender requirement."""
        if self.gender == SchoolGender.UNKNOWN:
            return False
        return self.gender in (SchoolGender.MIXED, requirement)

    def accepts_age(self, child_age: int) -> bool:
        phase_key = self.phase.lower()
        if phase_key in self._PHASE_RANGES:
            low, high = self._PHASE_RANGES[phase_key]
            return low <= child_age <= high
        too_young = self.statutory_low_age is not None and child_age < self.statutory_low_age
        too_old = self.statutory_high_age is not None and child_age > self.statutory_high_age
        return not too_young and not too_old


def _try_geopoint(lat: str, lng: str) -> GeoPoint | None:
    """Parse a GIAS coordinate pair; None when the cells aren't numeric."""
    try:
        return GeoPoint(float(lat), float(lng))
    except (ValueError, TypeError) as e:
        logger.debug("non-numeric GIAS coordinates lat=%s lng=%s ignored: %s", lat, lng, e)
        return None


def _parse_gender(raw: str) -> SchoolGender:
    """Map a GIAS gender label to SchoolGender; unknown labels become UNKNOWN."""
    try:
        return SchoolGender(raw)
    except ValueError as e:
        logger.debug("unrecognised school gender %r — defaulting to UNKNOWN: %s", raw, e)
        return SchoolGender.UNKNOWN
