# lucidlint: ignore duplicate-module sibling request DTOs share a shape by design — no fork (review-log)
"""The searchNearby body's location restriction."""

from __future__ import annotations

from houses.apis.places_circle import PlacesCircle
from serialization import SerializableAsDict


class PlacesRestriction(SerializableAsDict):
    """The searchNearby restriction — the fields ARE the request's body keys."""

    circle: PlacesCircle
