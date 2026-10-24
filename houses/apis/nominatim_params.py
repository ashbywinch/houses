"""The Nominatim search request's wire parameters."""

from __future__ import annotations

from serialization import SerializableAsDict


class NominatimParams(SerializableAsDict):
    """The Nominatim search query — the fields ARE the request's parameters."""

    q: str
    format: str
    limit: int
