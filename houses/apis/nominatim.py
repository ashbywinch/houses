"""Nominatim — free geocoding (1 req/s hard policy limit)."""

from __future__ import annotations

from dataclasses import dataclass

from houses import apigw
from houses.apis.transport import BaseApi, FetchArgs
from houses.geopoint import GeoPoint
from houses.web.json_utils import WirePayload


@dataclass(frozen=True)
class NominatimParams(WirePayload):
    q: str
    format: str
    limit: int


class NominatimApi(BaseApi):
    profile = apigw.NOMINATIM
    url = "https://nominatim.openstreetmap.org/search"

    async def geocode(self, query: str, *, _client_factory=None) -> GeoPoint | None:
        """Place-name geocode; None = not found."""
        params = NominatimParams(q=f"{query}, UK", format="json", limit=1)
        req = FetchArgs(
            "GET",
            self.url,
            params=params,
            headers={"User-Agent": "HousesApp/1.0"},
            _client_factory=_client_factory,
        )
        data = await self._fetch(req)
        if not data:
            return None
        try:
            return GeoPoint(float(data[0]["lat"]), float(data[0]["lon"]))
        except (KeyError, IndexError, TypeError):
            return None
