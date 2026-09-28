"""Google Places search (key on the wire, never the cache key)."""

from __future__ import annotations

from dataclasses import dataclass

from houses import apigw
from houses.apis.transport import BaseApi, FetchArgs
from houses.settings import settings
from houses.web.json_utils import WirePayload


@dataclass(frozen=True)
class PlacesCenter(WirePayload):
    latitude: float
    longitude: float


@dataclass(frozen=True)
class PlacesCircle(WirePayload):
    center: PlacesCenter
    radius: float


@dataclass(frozen=True)
class PlacesRestriction(WirePayload):
    circle: PlacesCircle


@dataclass(frozen=True)
class PlacesSearchBody(WirePayload):
    included_types: list[str]
    max_result_count: int
    location_restriction: PlacesRestriction


@dataclass(frozen=True)
class PlacesResult:
    """One Places result — the display name + coordinates the UI shows."""

    name: str
    latitude: float | None
    longitude: float | None
    types: list[str]


class GooglePlacesApi(BaseApi):
    profile = apigw.GOOGLE
    url = "https://places.googleapis.com/v1/places:searchNearby"

    @staticmethod
    def _api_headers() -> dict[str, str]:
        return {"X-Goog-Api-Key": settings.google_maps_api_key}

    async def search_nearby(
        self,
        lat: float,
        lng: float,
        *,
        types: list[str],
        radius_m: float = 1000.0,
        _client_factory=None,
    ) -> list[PlacesResult]:
        """Nearby places of the given types — parsed, caller-ready."""
        body = PlacesSearchBody(
            included_types=types,
            max_result_count=5,
            location_restriction=PlacesRestriction(
                circle=PlacesCircle(center=PlacesCenter(latitude=lat, longitude=lng), radius=radius_m)
            ),
        )
        req = FetchArgs(
            "POST",
            self.url,
            body=body,
            headers={
                **self._api_headers(),
                "X-Goog-FieldMask": "places.displayName,places.types,places.location",
                "Content-Type": "application/json",
            },
            _client_factory=_client_factory,
        )
        data = await self._fetch(req)
        if not data:
            return []
        results = []
        for p in data.get("places", []):
            loc = p.get("location") or {}
            name = (p.get("displayName") or {}).get("text", "")
            results.append(
                PlacesResult(
                    name=name,
                    latitude=loc.get("latitude"),
                    longitude=loc.get("longitude"),
                    types=p.get("types", []),
                )
            )
        return results
