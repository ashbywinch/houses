"""Google Places search (key on the wire, never the cache key)."""

from __future__ import annotations

from houses import apigw
from houses.apigw import FetchArgs
from houses.apis.base_api import BaseApi
from houses.apis.places_center import PlacesCenter
from houses.apis.places_circle import PlacesCircle
from houses.apis.places_restriction import PlacesRestriction
from houses.apis.places_result import PlacesResult
from houses.apis.places_search_body import PlacesSearchBody
from houses.settings import settings


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
        return [PlacesResult.from_place(place) for place in data.get("places", [])]
