"""One Google Places result — a name, where it is, and what kind of thing it is."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from houses.geopoint import GeoPoint

# What Places returns besides destinations: areas and transit hubs. A
# "locality" is a town, an "administrative_area_level_3" a borough, a
# "bus_stop" a point you pass through — none is somewhere you walk TO.
NOT_A_DESTINATION_TYPES = frozenset(
    {
        "transit_station",
        "bus_stop",
        "bus_station",
        "locality",
        "administrative_area_level_3",
        "administrative_area_level_4",
    }
)


@dataclass(frozen=True)
class PlacesResult:
    """One Google Places result: a name, its point, and the types Google gives it."""

    name: str
    location: GeoPoint | None
    types: list[str]

    @classmethod
    def from_place(cls, place: Any) -> PlacesResult:
        """Read one place from a Places response body."""
        location = place.get("location") or {}
        latitude, longitude = location.get("latitude"), location.get("longitude")
        return cls(
            name=(place.get("displayName") or {}).get("text", ""),
            # Places omits the point when it has none: no location, never (0, 0).
            location=GeoPoint(latitude, longitude) if latitude is not None and longitude is not None else None,
            types=place.get("types") or [],
        )

    def is_destination(self) -> bool:
        """Whether this is somewhere you would walk TO, not a hub or an area."""
        return not NOT_A_DESTINATION_TYPES.intersection(self.types)
