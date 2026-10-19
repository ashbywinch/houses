"""The ORS Pelias search request's wire parameters."""

from __future__ import annotations

from serialization import SerializableAsDict


class OrsSearchParams(SerializableAsDict):
    """The Pelias search query — the fields ARE the request's parameters."""

    text: str
    size: int
