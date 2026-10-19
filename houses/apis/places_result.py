"""One Google Places result — the display name + coordinates the UI shows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlacesResult:
    """One Places result — the display name + coordinates the UI shows."""

    name: str
    latitude: float | None
    longitude: float | None
    types: list[str]