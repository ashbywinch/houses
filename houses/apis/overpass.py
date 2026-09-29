"""OpenStreetMap Overpass — nearby amenity query."""

from __future__ import annotations

from dataclasses import dataclass

from houses import apigw
from houses.apis.transport import BaseApi, FetchArgs
from houses.web.json_utils import WirePayload


@dataclass(frozen=True)
class OverpassParams(WirePayload):
    data: str


@dataclass(frozen=True)
class OverpassElement:
    tags: dict[str, str]
    latitude: float | None
    longitude: float | None
    center: tuple[float, float] | None


@dataclass(frozen=True)
class OverpassResponse:
    elements: list[OverpassElement]


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
