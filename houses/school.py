from __future__ import annotations

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
    url: str = ""

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
    def from_GIAS_row(cls, row: dict) -> School:
        lat = row.get(cls._COL_LAT)
        lng = row.get(cls._COL_LNG)
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
            coords=GeoPoint(float(lat), float(lng)) if lat and lng else None,
            url=(f"https://get-information-schools.service.gov.uk"
                  f"/Establishments/Establishment/Details/{urn}") if urn else "",
        )

    def accepts(self, requirement: SchoolGender) -> bool:
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
