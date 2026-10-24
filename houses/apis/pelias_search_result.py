"""The Pelias geocoder's answer: the places it offers for a query."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from houses.apis.response_rows import rows_under
from houses.geopoint import GeoPoint

# The layer that means "not a place": Pelias's country itself.
NON_PLACE_LAYERS = frozenset({"country"})


@dataclass(frozen=True)
class PeliasSearchResult:
    """The places Pelias offers for a query, best first.

    Pelias answers a query it cannot resolve with its own "United Kingdom"
    fallback — ``match_type="fallback"``, ``layer="country"``, confidence 0.1,
    always the same point (54.75844,-2.69531). That is not a place: accepting it
    put a Didcot property in Cumbria, whose nearest station was Lazonby and
    which therefore had no rail fare to London, with a school walk measured from
    the wrong county. ``location()`` skips it, so the caller's cascade moves on
    to the next geocoder.
    """

    features: list[Any]

    @classmethod
    def from_response(cls, response: Any) -> PeliasSearchResult:
        """Read a Pelias response body."""
        return cls(features=rows_under(response, "features"))

    def location(self) -> GeoPoint | None:
        """The first place here that is a real place; ``None`` = try the next geocoder."""
        for feature in self.features:
            properties = feature.get("properties") or {}
            # Pelias says "I could not resolve this" with a country-level
            # feature, always at one fixed point.
            if properties.get("match_type") == "fallback" or properties.get("layer") in NON_PLACE_LAYERS:
                continue
            lng, lat = feature["geometry"]["coordinates"]
            return GeoPoint(lat, lng)
        return None

    def town_name(self) -> str | None:
        """The settlement this answer names; ``None`` = it named no town.

        The reverse lookup's answer: Pelias labels a point with the locality
        (a town) or the borough (a London borough). A fallback names the
        country, so it is skipped here the same way ``location()`` skips it.
        """
        for feature in self.features:
            properties = feature.get("properties") or {}
            if properties.get("match_type") == "fallback" or properties.get("layer") in NON_PLACE_LAYERS:
                continue
            town = properties.get("locality") or properties.get("borough")
            if town:
                return str(town)
        return None
