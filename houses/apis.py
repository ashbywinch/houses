"""One class per external API — the ONLY thing callers touch.

No caller here knows HTTP exists. Each API class encapsulates:

* its **settings** (which env key, what format),
* its **state** (the pacing + daily-quota gate for its profile),
* its **actual API details** (endpoint URLs, wire shapes, auth headers,
  response parsing into named domain types).

A caller does ``minutes = await ors.directions(origin, station)`` and gets
a domain value back. The mechanics live in :mod:`houses.apigw` (the shared
transport: cache, pace, quota, typed errors); these classes are the thin
domain layer over it — and the place an API's quirks live, once.

Adding an API = one class, one profile constant in :mod:`houses.apigw`,
one module-level instance. Callers then import the instance and call its
methods.

Request wire shapes are plain dataclasses: the transport serializes them
via ``dataclasses.asdict`` (nested shapes included), so API classes carry
the field names as documentation and never hand-build response dicts.
Provider payloads are parsed INSIDE the class into named response types.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from houses import apigw
from houses.geopoint import GeoPoint
from houses.settings import settings
from houses.web.json_utils import WirePayload

logger = logging.getLogger(__name__)

SECONDS_PER_MINUTE = 60.0


@dataclass(frozen=True)
class FetchArgs:
    """One guarded external call: what to send and where.

    A parameter object for the transport seam — every API method builds
    exactly one of these, then `_fetch` translates the transport errors.
    """

    method: str
    url: str
    params: Any = None
    body: Any = None
    headers: dict[str, str] | None = None
    wire_params: dict[str, str] | None = None
    _client_factory: Any = None


class BaseApi:
    """Shared transport access: one error translation + the DI seam."""

    profile: apigw.ApiProfile

    async def _fetch(self, req: FetchArgs) -> Any:
        """The transport call; DailyQuotaError and HTTP are translated here.

        ``DailyQuotaError`` becomes ``None`` (a caller's keep-fallback
        signal). Transient (429/5xx) and auth/permanent HTTP errors still
        raise — the DAG classifier owns those decisions.
        """
        try:
            return await apigw.api_fetch(
                req.method,
                req.url,
                api=self.profile,
                params=req.params,
                body=req.body,
                headers=req.headers,
                wire_params=req.wire_params,
                _client_factory=req._client_factory,
            )
        except apigw.DailyQuotaError:
            return None


# ═══ ORS — openrouteservice (directions + geocode, one key) ═══════


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
            "GET", self.geocode_url, params=params,
            headers=self._auth_headers(), _client_factory=_client_factory,
        )
        data = await self._fetch(req)
        if not data:
            return None
        features = data.get("features", [])
        if not features:
            return None
        lng, lat = features[0]["geometry"]["coordinates"]
        return GeoPoint(lat, lng)

    async def reverse_geocode(
        self, lat: float, lng: float, *, _client_factory=None
    ) -> GeoPoint | None:
        """Nearest settlement centre from coordinates."""
        url = self.geocode_url.replace("/search", "/reverse")
        params = OrsReverseParams(
            point_lat=lat, point_lon=lng, size=1, boundary_country="GBR"
        )
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
    ) -> int | None:
        """Minutes between two points for the given profile; None when the
        key is quota-exhausted (the caller keeps its fallback)."""
        url = f"{self.directions_url}/{mode}"
        body = OrsDirectionsBody(
            coordinates=[[origin.lon, origin.lat], [destination.lon, destination.lat]],
            units="km",
        )
        req = FetchArgs(
            "POST", url, body=body,
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            _client_factory=_client_factory,
        )
        try:
            data = await self._fetch(req)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # no route exists between these points (e.g. no road
                # connection): keep the walk leg, never an impossible pill
                logger.warning(
                    "ORS found no %s route between %s and %s",
                    mode, origin, destination,
                )
                return None
            raise
        if not data:
            return None
        try:
            return round(data["routes"][0]["summary"]["duration"] / SECONDS_PER_MINUTE)
        except (KeyError, IndexError, TypeError):
            logger.warning("ORS directions response for %s lacked a usable route summary", mode)
            return None


ors = ORSApi()


# ═══ Nominatim — free geocoding (1 req/s) ═════════════════════════


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
            "GET", self.url, params=params,
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


nominatim = NominatimApi()


# ═══ Google — geocode + Places (key on the wire, never the cache key) ═════


@dataclass(frozen=True)
class GoogleGeocodeParams(WirePayload):
    address: str


class GoogleGeocodeApi(BaseApi):
    profile = apigw.GOOGLE
    url = "https://maps.googleapis.com/maps/api/geocode/json"

    async def geocode(self, address: str, *, _client_factory=None) -> GeoPoint | None:
        params = GoogleGeocodeParams(address=f"{address}, UK")
        req = FetchArgs(
            "GET", self.url, params=params,
            wire_params={"key": settings.google_maps_api_key},
            _client_factory=_client_factory,
        )
        data = await self._fetch(req)
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


google_geocode = GoogleGeocodeApi()


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
        self, lat: float, lng: float, *, types: list[str], radius_m: float = 1000.0,
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
            "POST", self.url, body=body,
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


places = GooglePlacesApi()


# ═══ postcodes.io ═══════════════════════════════════════════════


class PostcodesApi(BaseApi):
    profile = apigw.POSTCODESIO
    url = "https://api.postcodes.io/postcodes"
    outcode_url = "https://api.postcodes.io/outcodes"

    async def geocode(self, postcode: str, *, _client_factory=None) -> GeoPoint | None:
        key = postcode.strip().upper()
        if not key:
            return None
        url = (
            f"{self.outcode_url}/{key}"
            if not any(c.isdigit() for c in key)
            else f"{self.url}/{key}"
        )
        req = FetchArgs("GET", url, _client_factory=_client_factory)
        data = await self._fetch(req)
        if not data:
            return None
        result = data.get("result")
        if not result:
            return None
        return GeoPoint(result["latitude"], result["longitude"])


postcodes = PostcodesApi()


# ═══ UK gov EPC register ═════════════════════════════════════════


@dataclass(frozen=True)
class EpcSearchResult:
    """The EPC register's certificate rows, as returned by the search."""

    certificates: list[Any]


class EpcApi(BaseApi):
    profile = apigw.GOV_EPC

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.epc_bearer_token}"}

    async def search(
        self, url: str, params: WirePayload, *, _client_factory=None
    ) -> EpcSearchResult:
        """Register search; the caller matches its building among the rows."""
        req = FetchArgs(
            "GET", url, params=params,
            headers={"Accept": "application/json", **self._auth_headers()},
            _client_factory=_client_factory,
        )
        data = await self._fetch(req)
        if not isinstance(data, dict):
            return EpcSearchResult(certificates=[])
        return EpcSearchResult(certificates=data.get("data", []))


epc = EpcApi()


# ═══ OpenStreetMap Overpass ══════════════════════════════════════


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
        req = FetchArgs(
            "GET", self.url, params=OverpassParams(data=ql),
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


overpass = OverpassApi()