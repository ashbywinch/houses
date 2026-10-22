"""Google Maps geocoding (key on the wire, never the cache key)."""

from __future__ import annotations

import logging
from typing import NotRequired, TypedDict, cast

from houses import apigw
from houses.apigw import FetchArgs
from houses.apis.base_api import BaseApi
from houses.apis.google_geocode_params import GoogleGeocodeParams
from houses.geopoint import GeoPoint
from houses.settings import settings

logger = logging.getLogger(__name__)


class _GoogleLocation(TypedDict):
    """A Google result's point."""

    lat: float
    lng: float


class _GoogleGeometry(TypedDict):
    location: _GoogleLocation


class _GoogleResult(TypedDict):
    """One Google geocode result: its point and what kind of place it is."""

    types: list[str]
    geometry: _GoogleGeometry
    formatted_address: NotRequired[str]


class _GoogleResponse(TypedDict):
    """A Google geocode response: its status and results, best first."""

    results: list[_GoogleResult]
    status: NotRequired[str]


class GoogleGeocodeApi(BaseApi):
    profile = apigw.GOOGLE
    url = "https://maps.googleapis.com/maps/api/geocode/json"

    async def geocode(self, address: str, *, _client_factory=None, _no_cache: bool = False) -> GeoPoint | None:
        """Geocode a free-form UK address; None = try the next provider."""
        params = GoogleGeocodeParams(address=f"{address}, UK")
        req = FetchArgs(
            "GET",
            self.url,
            params=params,
            wire_params={"key": settings.google_maps_api_key},
            _client_factory=_client_factory,
            no_cache=_no_cache,
        )
        data = await self._fetch(req)
        if data and data.get("status") == "OVER_QUERY_LIMIT":
            # Google reports the daily limit in the BODY (HTTP 200); a 403 is
            # handled by the profile's quota_statuses. Either way the key is
            # unusable for this request run — mark it and keep the fallback.
            apigw.GATE.mark_quota_exhausted(self.profile)
            logger.warning("Google Maps OVER_QUERY_LIMIT — marked exhausted for this request run")
            return None
        if not data or data.get("status") != "OK":
            logger.warning(
                "Google Maps geocode failed: status=%s msg=%s",
                data.get("status") if data else "no response",
                (data or {}).get("error_message", ""),
            )
            return None
        return self._first_usable_result(cast(_GoogleResponse, data))

    @staticmethod
    def _first_usable_result(data: _GoogleResponse) -> GeoPoint | None:
        """Google's first result that is a real place; ``None`` = next provider.

        An address Google cannot resolve comes back as the country itself —
        ``types: ["country", "political"]``, formatted_address "United Kingdom"
        — centred on the GB centroid, which is not a location.
        """
        for result in data.get("results") or []:
            if "country" in result["types"]:
                continue
            loc = result["geometry"]["location"]
            return GeoPoint(loc["lat"], loc["lng"])
        return None
