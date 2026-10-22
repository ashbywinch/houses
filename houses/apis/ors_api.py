"""ORS — openrouteservice (directions + Pelias geocode, one key)."""

from __future__ import annotations

import logging
from typing import NotRequired, TypedDict, cast

import httpx
from pint import Quantity

from houses import apigw
from houses.apigw import FetchArgs
from houses.apis.base_api import BaseApi
from houses.apis.ors_directions_body import OrsDirectionsBody
from houses.apis.ors_reverse_params import OrsReverseParams
from houses.apis.ors_search_params import OrsSearchParams
from houses.geopoint import GeoPoint
from houses.ors_endpoints import ORS_DIRECTIONS, PELIAS_REVERSE, PELIAS_SEARCH
from houses.settings import settings

logger = logging.getLogger(__name__)


class _PeliasGeometry(TypedDict):
    """A Pelias feature's point, ``[lon, lat]``."""

    coordinates: list[float]


class _PeliasProperties(TypedDict):
    """The pedigree of a Pelias feature — what makes it a place, or not."""

    match_type: NotRequired[str]
    layer: NotRequired[str]
    name: NotRequired[str]
    country: NotRequired[str]
    confidence: NotRequired[float]
    accuracy: NotRequired[str]


class _PeliasFeature(TypedDict):
    geometry: _PeliasGeometry
    properties: _PeliasProperties


class _PeliasResponse(TypedDict):
    """A Pelias search/reverse response: the features it offers, best first."""

    features: list[_PeliasFeature]



class ORSApi(BaseApi):
    """ORS directions + Pelias geocoding (share the key: one quota flag)."""

    profile = apigw.ORS
    directions_url = ORS_DIRECTIONS
    geocode_url = PELIAS_SEARCH

    # Which Pelias layers count as "a town" for a reverse lookup. Unconstrained,
    # Pelias answers with the closest feature of ANY kind — for a house that is
    # its own street (or a school), i.e. a 0-minute walk, which walkability's
    # plausibility gate rejects: houses whose address-derived town is a district
    # name (London, South Oxfordshire, "Maidenhead Station Area") then silently
    # lost walk_to_town. Settlement layers only — locality (town), borough
    # (London boroughs), neighbourhood (suburbs). Pelias sorts by distance, so an
    # SW20 house resolves to West Barnes (10 min walk) rather than the London
    # centroid (210 min).
    SETTLEMENT_LAYERS = "locality,borough,neighbourhood"

    # The one no-route signal ORS returns for an impossible point pair.
    HTTP_NOT_FOUND = 404

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
        return self._first_usable_feature(cast(_PeliasResponse, data))

    @staticmethod
    def _first_usable_feature(data: _PeliasResponse) -> GeoPoint | None:
        """The first Pelias feature that is a real place; ``None`` = next provider.

        Pelias answers an address it cannot resolve with its own "United
        Kingdom" fallback — ``match_type="fallback"``, ``layer="country"``,
        confidence 0.1 — always at the same point (54.75844,-2.69531). That is
        not a location: accepting it put a Didcot property in Cumbria, whose
        nearest station was Lazonby and which therefore had no rail fare to
        London. Unresolved means the NEXT provider, never a country centroid.
        """
        for feature in data.get("features") or []:
            props = feature["properties"]
            if props.get("match_type") == "fallback" or props.get("layer") == "country":
                logger.debug("Pelias returned its 'United Kingdom' fallback — trying the next geocoder")
                continue
            lng, lat = feature["geometry"]["coordinates"]
            return GeoPoint(lat, lng)
        return None

    async def reverse_geocode(self, lat: float, lng: float, *, _client_factory=None) -> GeoPoint | None:
        """Nearest settlement centre from coordinates."""
        url = PELIAS_REVERSE
        params = OrsReverseParams(
            point_lat=lat,
            point_lon=lng,
            size=1,
            boundary_country="GBR",
            layers=self.SETTLEMENT_LAYERS,
        )
        req = FetchArgs(
            "GET",
            url,
            params=params,
            headers=self._auth_headers(),
            _client_factory=_client_factory,
        )
        data = await self._fetch(req)
        if not data:
            return None
        return self._first_usable_feature(data)

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
            if exc.response.status_code == self.HTTP_NOT_FOUND:
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