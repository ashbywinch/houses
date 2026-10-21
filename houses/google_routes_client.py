"""The Google Routes transport: the client, its options, and its wire.

One module, because the wire records and the call that carries them change
together — Google's request/response schema is this client's contract. The
interface (``GoogleRoutesClientLike``) sits beside the class it names.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import Field, SerializerFunctionWrapHandler, model_serializer

from dag.http_error import HttpError
from houses import apigw
from houses.api_cache import CacheRequest, cached_async_client
from houses.settings import settings
from serialization import SerializableAsDict, optional_parse


@dataclass(frozen=True)
class GoogleRoutesOptions:
    """HTTP options for a Google Routes POST: key, timeout, and test seams.

    The cache/client seams default to the module implementations so tests
    never monkeypatch ``houses.commute_router`` globals.
    """

    timeout: float = 10.0
    api_key: str | None = None
    client_factory: Callable[..., Any] | None = None
    set_cached_fn: Callable[..., Any] | None = None


class _LatLng(SerializableAsDict):
    """A Google Routes latitude/longitude pair (request wire shape)."""

    latitude: float
    longitude: float


class _WaypointLocation(SerializableAsDict):
    """The Google Routes waypoint's ``location`` block (request wire shape)."""

    lat_lng: _LatLng = Field(alias="latLng")


class _Waypoint(SerializableAsDict):
    """A Google Routes waypoint: a coordinate or a free-text address."""

    location: _LatLng | None = None
    address: str = ""

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> Mapping[str, Any]:
        """Exactly one of coordinate/address goes on the wire: Google rejects a
        waypoint carrying both, so the address key is dropped when a coordinate
        is given, and the coordinate block when it is not."""
        if self.location is not None:
            return {"location": _WaypointLocation(lat_lng=self.location).model_dump(by_alias=True)}
        return {"address": self.address}


class _RoutesBody(SerializableAsDict):
    """A Google Routes directions POST body — the record flows to the transport
    edge, which serializes it once via ``to_dict``."""

    origin: _Waypoint
    destination: _Waypoint
    travel_mode: str = Field(alias="travelMode")


@dataclass(frozen=True)
class _RoutesResponse:
    """The Google Routes directions response root — the {routes} wire shape."""

    routes: list[_Route]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> _RoutesResponse:
        # The transport seam hands back raw JSON, so the root arrives untyped;
        # every nested record below names its own wire shape.
        return cls(routes=[_Route.from_dict(route) for route in raw.get("routes") or []])


@dataclass(frozen=True)
class _Route:
    """A Google Routes route — the {duration, distanceMeters, legs} wire shape."""

    duration: str
    distance_meters: int
    legs: list[_RouteLeg]

    @classmethod
    def from_dict(cls, raw: dict) -> _Route:
        return cls(
            duration=raw.get("duration", "0s"),
            distance_meters=raw.get("distanceMeters", 0),
            legs=[_RouteLeg.from_dict(leg) for leg in raw.get("legs") or []],
        )


@dataclass(frozen=True)
class _RouteLeg:
    """A Google Routes route leg — the {steps} wire shape."""

    steps: list[_RouteStep]

    @classmethod
    def from_dict(cls, raw: dict) -> _RouteLeg:
        return cls(steps=[_RouteStep.from_dict(step) for step in raw.get("steps") or []])


@dataclass(frozen=True)
class _RouteStep:
    """A Google Routes leg step — the {travelMode, staticDuration, transitDetails} wire shape."""

    travel_mode: str | None
    static_duration: str
    transit_details: _RouteTransitDetails | None

    @classmethod
    def from_dict(cls, raw: dict) -> _RouteStep:
        return cls(
            travel_mode=raw.get("travelMode"),
            static_duration=raw.get("staticDuration", "0s"),
            transit_details=optional_parse(raw, "transitDetails", _RouteTransitDetails.from_dict),
        )


@dataclass(frozen=True)
class _RouteTransitDetails:
    """A Google Routes step's transitDetails — the {transitLine} wire shape."""

    transit_line: _RouteTransitLine | None

    @classmethod
    def from_dict(cls, raw: dict) -> _RouteTransitDetails:
        line = raw.get("transitLine")
        return cls(transit_line=_RouteTransitLine.from_dict(line) if line else None)


@dataclass(frozen=True)
class _RouteTransitLine:
    """A Google Routes transit line — the {nameShort, name, agencies} wire shape."""

    name_short: str | None
    name: str | None
    agencies: list[_RouteAgency]

    @classmethod
    def from_dict(cls, raw: dict) -> _RouteTransitLine:
        return cls(
            name_short=raw.get("nameShort"),
            name=raw.get("name"),
            agencies=[_RouteAgency.from_dict(agency) for agency in raw.get("agencies") or []],
        )


@dataclass(frozen=True)
class _RouteAgency:
    """A Google Routes transit agency — the {name} wire shape."""

    name: str | None

    @classmethod
    def from_dict(cls, raw: dict) -> _RouteAgency:
        return cls(name=raw.get("name"))


class GoogleRoutesClientLike(Protocol):
    """The transport seam ``GoogleRoutesClient`` satisfies — and the type tests
    stub out, so ``CommuteRouter`` never sees a real HTTP client."""

    async def post(
        self, body: SerializableAsDict, field_mask: str, *, options: GoogleRoutesOptions | None = None
    ) -> Mapping[str, Any] | None: ...


class GoogleRoutesClient:
    """Owns the raw Google Routes HTTP machinery: auth, caching, POST.

    Split out of ``CommuteRouter`` so the commute orchestration (walk /
    transit / drive choice) and the HTTP transport evolve independently.
    """

    GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
    HTTP_TOO_MANY_REQUESTS = 429

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client_factory: Callable[..., Any] | None = None,
        set_cached_fn: Callable[..., Any] | None = None,
    ):
        self.api_key: str | None = api_key
        self.client_factory: Callable[..., Any] | None = client_factory
        self.set_cached_fn: Callable[..., Any] | None = set_cached_fn

    def _merge_defaults(self, options: GoogleRoutesOptions | None) -> GoogleRoutesOptions:
        """Constructor seams apply unless a caller overrides per call."""
        base = options or GoogleRoutesOptions()
        return GoogleRoutesOptions(
            timeout=base.timeout,
            api_key=base.api_key if base.api_key is not None else self.api_key,
            client_factory=base.client_factory or self.client_factory,
            set_cached_fn=base.set_cached_fn or self.set_cached_fn,
        )

    @staticmethod
    def _raise_with_body(resp: httpx.Response) -> None:
        """``raise_for_status()`` with the response body appended to the error."""
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = resp.text[:1000]
            raise httpx.HTTPStatusError(f"{e} — {body}", request=e.request, response=e.response) from e

    async def post(
        self,
        body: SerializableAsDict,
        field_mask: str,
        *,
        options: GoogleRoutesOptions | None = None,
    ) -> Mapping[str, Any] | None:
        """POST to Google Routes API, caching responses and direct HTTP call.

        Raises ``ValueError`` if the API key is not configured.
        """
        options = self._merge_defaults(options)
        google_key = settings.google_maps_api_key if options.api_key is None else options.api_key
        if not google_key:
            raise ValueError("Google Maps API key not configured")

        # The Google Routes request headers: a keyed collection of the
        # provider's header names — pairs, never a record shape.
        headers = dict(
            (
                ("Content-Type", "application/json"),
                ("X-Goog-Api-Key", google_key),
                ("X-Goog-FieldMask", field_mask),
            )
        )
        
        # transport edge; legacy raw-dict callers pass through unchanged (coding-standards.md)
        payload = body.to_dict() if hasattr(body, "to_dict") else body
        key = json.dumps(payload, sort_keys=True)
        call = CacheRequest("POST", self.GOOGLE_ROUTES_URL, body=key)
        cached = call.cached()
        if cached is not None:
            return cached

        client_factory = options.client_factory or cached_async_client
        async with client_factory(timeout=options.timeout) as client:
            await apigw.GATE.pace(apigw.GOOGLE)
            resp = await client.post(self.GOOGLE_ROUTES_URL, json=payload, headers=headers)
            if resp.status_code == GoogleRoutesClient.HTTP_TOO_MANY_REQUESTS:
                raise HttpError(GoogleRoutesClient.HTTP_TOO_MANY_REQUESTS, "rate limited", headers=dict(resp.headers))
            self._raise_with_body(resp)
            data = resp.json()
            (options.set_cached_fn or CacheRequest.store)(call, data)
            return data




