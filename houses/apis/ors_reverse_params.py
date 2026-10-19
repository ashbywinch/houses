"""The ORS Pelias reverse-geocode request's wire parameters."""

from __future__ import annotations

from pydantic import Field

from serialization import SerializableAsDict


class OrsReverseParams(SerializableAsDict):
    """The Pelias reverse-geocode query.

    Pelias spells the point and the country boundary as dotted keys
    (``point.lat``, ``boundary.country``), which no field name can be — the
    alias says so per field, and ``to_dict()`` writes the wire key.
    """

    point_lat: float = Field(alias="point.lat")
    point_lon: float = Field(alias="point.lon")
    size: int
    boundary_country: str = Field(alias="boundary.country")
    layers: str
