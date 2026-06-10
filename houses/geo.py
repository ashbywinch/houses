"""Geographic coordinate primitives."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GeoPoint:
    """A geographic coordinate point with lat/lon in decimal degrees."""

    lat: float
    lon: float

    def distance_km_to(self, other: GeoPoint) -> float:
        """Great-circle distance in km using the haversine formula."""
        r = 6371.0
        dlat = math.radians(other.lat - self.lat)
        dlon = math.radians(other.lon - self.lon)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(self.lat)) * math.cos(math.radians(other.lat)) * math.sin(dlon / 2) ** 2
        )
        return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
