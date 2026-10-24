"""A single rail fare between two stations — the CSV row ``RailFareRegistry`` loads."""

from __future__ import annotations

from dataclasses import dataclass

from money import Money


@dataclass(frozen=True)
class RailFare:
    """A single fare between two stations."""

    origin_crs: str
    dest_crs: str
    single_fare_gbp: Money
