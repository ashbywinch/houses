"""One bus step's stops, as Google states them."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TransitStop:
    """One bus step's stops, as Google states them (names + coordinates)."""

    departure_name: str
    arrival_name: str
    departure_lat: float | None
    departure_lon: float | None
    arrival_lat: float | None
    arrival_lon: float | None
