"""OpenStreetMap Overpass — nearby amenity query."""

from __future__ import annotations

from houses import apigw
from houses.apigw import FetchArgs
from houses.apis.base_api import BaseApi
from houses.apis.overpass_element import OverpassElement
from houses.apis.overpass_params import OverpassParams
from houses.apis.overpass_response import OverpassResponse


class OverpassApi(BaseApi):
    profile = apigw.OVERPASS
    url = "https://overpass-api.de/api/interpreter"

    async def query(self, ql: str, *, _client_factory=None) -> OverpassResponse:
        """Run an Overpass QL query; parse the elements into named rows."""
        req = FetchArgs(
            "GET",
            self.url,
            params=OverpassParams(data=ql),
            headers={"Accept": "application/json", "User-Agent": "HousesApp/1.0"},
            _client_factory=_client_factory,
        )
        raw = await self._fetch(req)
        elements = []
        for e in (raw or {}).get("elements", []):
            center = e.get("center")
            elements.append(
                OverpassElement(
                    tags=e.get("tags", {}),
                    latitude=e.get("lat"),
                    longitude=e.get("lon"),
                    center=(center["lat"], center["lon"]) if center else None,
                )
            )
        return OverpassResponse(elements=elements)
