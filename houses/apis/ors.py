"""ORS — openrouteservice (directions + Pelias geocode, one key)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

import httpx
from pint import Quantity

from houses import apigw
from houses.apis.transport import BaseApi, FetchArgs
from houses.geopoint import GeoPoint
from houses.settings import settings
from houses.web.json_utils import WirePayload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrsSearchParams(WirePayload):
    text: str
    size: int


@dataclass(frozen=True)
class OrsReverseParams(WirePayload):
    point_lat: float
    point_lon: float
    size: int
    boundary_country: str
    # the Pelias reverse endpoint wants dotted keys
    wire_key_rewrites: ClassVar[dict[str, str]] = {"point_lat": "point.lat", "point_lon": "point.lon"}


@dataclass(frozen=True)
class OrsDirectionsBody(WirePayload):
    coordinates: list[list[float]]
    units: str


class ORSApi(BaseApi):
    """ORS directions + Pelias geocoding (share the key: one quota flag)."""

    profile = apigw.ORS
    directions_url = "https://api.openrouteservice.org/v2/directions"
    geocode_url = "https://api.openrouteservice.org/geocode/search"

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        return {"Authorization": settings.ors_api_key}

    async def geocode(self, address: str, *, _client_factory=None) -> GeoPoint | None:
        """Geocode a free-form UK address; None = try the next provider."""
        params = OrsSearchParams(text=f"{address}, UK", size=1)
        req = FetchArgs(
            "GET",
            self.geocode_url,
            params=params,
            headers=self._auth_headers(),
            _client_factory=_client_factory,
        )
        data = await self._fetch(req)
        if not data:
            return None
        features = data.get("features", [])
        if not features:
            return None
        lng, lat = features[0]["geometry"]["coordinates"]
        return GeoPoint(lat, lng)

    async def reverse_geocode(self, lat: float, lng: float, *, _client_factory=None) -> GeoPoint | None:
        """Nearest settlement centre from coordinates."""
        url = self.geocode_url.replace("/search", "/reverse")
        params = OrsReverseParams(point_lat=lat, point_lon=lng, size=1, boundary_country="GBR")
        req = FetchArgs("GET", url, params=params, _client_factory=_client_factory)
        data = await self._fetch(req)
        if not data:
            return None
        features = data.get("features", [])
        if not features:
            return None
        return GeoPoint(
            features[0]["geometry"]["coordinates"][1],
            features[0]["geometry"]["coordinates"][0],
        )

    async def directions(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        *,
        mode: str = "driving-car",
        _client_factory=None,
        _no_cache: bool = False,
    ) -> int | None:
        """Minutes between two points for the given profile; None when the
        key is quota-exhausted (the caller keeps its fallback).

        The API reports ``duration`` in seconds (a wire number); the DAG
        stores minutes, so the unit conversion is the pint computation and
        the bare int is the serialization boundary.
        """
        url = f"{self.directions_url}/{mode}"
        body = OrsDirectionsBody(
            coordinates=[[origin.lon, origin.lat], [destination.lon, destination.lat]],
            units="km",
        )
        req = FetchArgs(
            "POST",
            url,
            body=body,
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            _client_factory=_client_factory,
            no_cache=_no_cache,
        )
        try:
            data = await self._fetch(req)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # no route exists between these points (e.g. no road
                # connection): keep the walk leg, never an impossible pill
                logger.warning(
                    "ORS found no %s route between %s and %s",
                    mode,
                    origin,
                    destination,
                )
                return None
            raise
        if not data:
            return None
        try:
            duration_s = data["routes"][0]["summary"]["duration"]
            minutes_q = Quantity(duration_s, "second").to("minute")
            return round(minutes_q.magnitude)
        except (KeyError, IndexError, TypeError, ValueError):
            logger.warning("ORS directions response for %s lacked a usable route summary", mode)
            return None
