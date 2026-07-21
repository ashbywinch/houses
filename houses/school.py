from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import ClassVar

from houses.geo import GeoPoint
from houses.school_gender import SchoolGender


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
    def _try_int(raw: str) -> int | None:
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

    @classmethod
    def from_GIAS_row(cls, row: dict) -> School:  # noqa: N802
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
            try:
                corrected = GeoPoint(float(corr_lat), float(corr_lng))
            except (ValueError, TypeError):
                corrected = None
            if corrected is not None and original is not None:
                if original.distance_km_to(corrected) < 100:
                    coords = corrected
            elif corrected is not None:
                coords = corrected

        raw_gender = (row.get(cls._COL_GENDER) or "").strip().lower()
        try:
            gender = SchoolGender(raw_gender)
        except ValueError:
            gender = SchoolGender.UNKNOWN
        urn = (row.get(cls._COL_URN) or "").strip()
        return cls(
            urn=urn,
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
            coords=coords,
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
