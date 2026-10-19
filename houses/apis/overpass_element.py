"""One Overpass element: an amenity with optional coordinates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OverpassElement:
    tags: dict[str, str]
    latitude: float | None
    longitude: float | None
    center: tuple[float, float] | None