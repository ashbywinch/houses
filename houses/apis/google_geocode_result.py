"""The Google geocoder's answer: the places it offers for a query."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from houses.apis.response_rows import rows_under
from houses.geopoint import GeoPoint

# Google's "not a place" result type: the country itself.
NON_PLACE_TYPES = frozenset({"country"})


@dataclass(frozen=True)
class GoogleGeocodeResult:
    """The places Google offers for a geocode query, best first.

    An address Google cannot resolve comes back as the country it sits in —
    ``types: ["country", "political"]``, ``formatted_address: "United Kingdom"``
    — centred on the GB centroid, which is not a location. ``location()`` skips
    it, so the caller's cascade moves on to the next geocoder.
    """

    results: list[Any]

    @classmethod
    def from_response(cls, response: Any) -> GoogleGeocodeResult:
        """Read a Google geocode response body."""
        return cls(results=rows_under(response, "results"))

    def location(self) -> GeoPoint | None:
        """The first place here that is a real place; ``None`` = try the next geocoder."""
        for result in self.results:
            types = result.get("types") or []
            if NON_PLACE_TYPES.intersection(types):
                continue
            location = result["geometry"]["location"]
            return GeoPoint(location["lat"], location["lng"])
        return None
