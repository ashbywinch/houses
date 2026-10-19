# lucidlint: ignore duplicate-module sibling request DTOs share a shape by design — no fork (review-log)
"""The Overpass query request's wire parameters."""

from __future__ import annotations

from serialization import SerializableAsDict


class OverpassParams(SerializableAsDict):
    """The Overpass query — the fields ARE the request's parameters."""

    data: str
