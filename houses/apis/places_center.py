# lucidlint: ignore duplicate-module sibling request DTOs share a shape by design — no fork (review-log)
"""The searchNearby body's center location."""

from __future__ import annotations

from serialization import SerializableAsDict


class PlacesCenter(SerializableAsDict):
    """The searchNearby center — the fields ARE the request's body keys."""

    latitude: float
    longitude: float
