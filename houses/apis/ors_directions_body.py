"""The ORS directions request body."""

from __future__ import annotations

from serialization import SerializableAsDict


class OrsDirectionsBody(SerializableAsDict):
    """The ORS directions body — the fields ARE the request's body keys."""

    coordinates: list[list[float]]
    units: str
