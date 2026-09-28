"""Google Maps geocoding (key on the wire, never the cache key)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from houses import apigw
from houses.apis.transport import BaseApi, FetchArgs
from houses.geopoint import GeoPoint
from houses.settings import settings
from houses.web.json_utils import WirePayload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GoogleGeocodeParams(WirePayload):
    address: str


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
        results = data.get("results") or []
        if not results:
            return None
        loc = results[0]["geometry"]["location"]
        return GeoPoint(loc["lat"], loc["lng"])
