"""The Google Maps geocode request's wire parameters."""

from __future__ import annotations

from serialization import SerializableAsDict


class GoogleGeocodeParams(SerializableAsDict):
    """The Google Maps geocode query — the fields ARE the request's parameters."""

    address: str
