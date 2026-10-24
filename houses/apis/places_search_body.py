"""The Google Places searchNearby request body."""

from __future__ import annotations

from houses.apis.places_restriction import PlacesRestriction
from serialization import SerializableAsDict


class PlacesSearchBody(SerializableAsDict):
    """The searchNearby body — the fields ARE the request's body keys."""

    included_types: list[str]
    max_result_count: int
    location_restriction: PlacesRestriction
