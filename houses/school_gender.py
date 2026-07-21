from __future__ import annotations

from enum import StrEnum


class SchoolGender(StrEnum):
    """GIAS 'Gender (name)' column values — also used as query requirements."""

    BOYS = "boys"
    GIRLS = "girls"
    MIXED = "mixed"
    UNKNOWN = "unknown"
