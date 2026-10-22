"""One Overpass element: an amenity with optional coordinates."""

from __future__ import annotations

from dataclasses import dataclass

from houses.geopoint import GeoPoint


@dataclass(frozen=True)
class OverpassElement:
    tags: dict[str, str]
    latitude: float | None
    longitude: float | None
    center: tuple[float, float] | None

    def location(self) -> GeoPoint | None:
        """The element's point: node coordinates, or a way's centre.

        Overpass returns nodes with their own coordinates and ways with a
        ``center``; the query asks for both, so both are possible. ``None``
        means neither was given — never a made-up point.
        """
        latitude = self.latitude or (self.center[0] if self.center else None)
        longitude = self.longitude or (self.center[1] if self.center else None)
        if latitude is None or longitude is None:
            return None
        return GeoPoint(latitude, longitude)
