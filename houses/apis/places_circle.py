# lucidlint: ignore duplicate-module sibling request DTOs share a shape by design — no fork (review-log)
"""The searchNearby body's circle around a center."""

from __future__ import annotations

from houses.apis.places_center import PlacesCenter
from serialization import SerializableAsDict


class PlacesCircle(SerializableAsDict):
    """The searchNearby circle — the fields ARE the request's body keys."""

    center: PlacesCenter
    radius: float
