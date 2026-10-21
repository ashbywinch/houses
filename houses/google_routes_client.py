"""The Google Routes transport: the client and its wire.

One module, because the wire records and the call that carries them change
together — Google's request/response schema is this client's contract. The
interface (``GoogleRoutesClientLike``) sits beside the class it names; the
HTTP options live in ``houses/google_routes_options.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pint import Quantity
from pydantic import Field, SerializerFunctionWrapHandler, model_serializer

from dag.http_error import HttpError
from houses import apigw
from houses.api_cache import CacheRequest, cached_async_client
from houses.bus_route import BusRoute
from houses.commute import JourneyLeg, LegMode
from houses.geopoint import GeoPoint
from houses.google_route import GoogleRoute
from houses.google_routes_options import GoogleRoutesOptions
from houses.settings import settings
from houses.transit_stop import TransitStop
from serialization import SerializableAsDict, omit_when_none, optional_parse


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


def _parse_coord(loc: str) -> _Waypoint | None:
    """Parse a ``"lat,lon"`` string into a location waypoint, else None."""
    try:
        lat, lon = loc.split(",", 1)
        return _Waypoint(location=_LatLng(latitude=float(lat), longitude=float(lon)))
    except (ValueError, TypeError):
        return None


def _address_waypoint(loc: str | GeoPoint) -> _Waypoint:
    """Build a Google Routes waypoint from a postcode string or GeoPoint."""
    if isinstance(loc, GeoPoint):
        return _Waypoint(location=_LatLng(latitude=loc.lat, longitude=loc.lon))
    # A "lat,lon" string is a point; anything else is a free-text address.
    if "," in loc:
        waypoint = _parse_coord(loc)
        if waypoint is not None:
            return waypoint
    return _Waypoint(address=loc)


class _TransitPreferences(SerializableAsDict):
    """The Google Routes transitPreferences block (request wire shape)."""

    routing_preference: str = Field(alias="routingPreference")


class _RoutesBody(SerializableAsDict):
    """A Google Routes directions POST body — the record flows to the transport
    edge, which serializes it once via ``to_dict``."""

    origin: _Waypoint
    destination: _Waypoint
    travel_mode: str = Field(alias="travelMode")
    transit_preferences: _TransitPreferences | None = Field(
        default=None, alias="transitPreferences", exclude_if=omit_when_none
    )
    compute_alternative_routes: bool | None = Field(
        default=None, alias="computeAlternativeRoutes", exclude_if=omit_when_none
    )


@dataclass(frozen=True)
class _RoutesResponse:
    """The Google Routes directions response root — the {routes} wire shape."""

    routes: list[_Route]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> _RoutesResponse:
        # The transport seam hands back raw JSON, so the root arrives untyped;
        # every nested record below names its own wire shape.
        return cls(routes=[_Route.from_dict(route) for route in raw.get("routes") or []])


def _first_route(data: Mapping[str, Any]) -> _Route | None:
    """The response's first route, or None when Google sent none — the
    orchestration's "no route for this journey"."""
    routes = _RoutesResponse.from_dict(data).routes
    return routes[0] if routes else None


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


def _duration_seconds(duration: str) -> int:
    """Google states a duration as a ``"123s"`` string; the orchestration
    wants whole seconds."""
    return int(str(duration).removesuffix("s"))


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
    """A Google Routes step's transitDetails — the {transitLine, stopDetails}
    wire shape."""

    transit_line: _RouteTransitLine | None
    stop_details: _RouteStopDetails | None

    @classmethod
    def from_dict(cls, raw: dict) -> _RouteTransitDetails:
        return cls(
            transit_line=optional_parse(raw, "transitLine", _RouteTransitLine.from_dict),
            stop_details=optional_parse(raw, "stopDetails", _RouteStopDetails.from_dict),
        )


@dataclass(frozen=True)
class _RouteTransitLine:
    """A Google Routes transit line — the {nameShort, name, agencies, vehicle} wire shape."""

    name_short: str | None
    name: str | None
    agencies: list[_RouteAgency]
    vehicle: _RouteVehicle | None

    @classmethod
    def from_dict(cls, raw: dict) -> _RouteTransitLine:
        return cls(
            name_short=raw.get("nameShort"),
            name=raw.get("name"),
            agencies=[_RouteAgency.from_dict(agency) for agency in raw.get("agencies") or []],
            vehicle=optional_parse(raw, "vehicle", _RouteVehicle.from_dict),
        )


@dataclass(frozen=True)
class _RouteAgency:
    """A Google Routes transit agency — the {name} wire shape."""

    name: str | None

    @classmethod
    def from_dict(cls, raw: dict) -> _RouteAgency:
        return cls(name=raw.get("name"))


@dataclass(frozen=True)
class _RouteVehicle:
    """A Google Routes transit vehicle — the {type} wire shape."""

    type: str | None

    @classmethod
    def from_dict(cls, raw: dict) -> _RouteVehicle:
        return cls(type=raw.get("type"))


@dataclass(frozen=True)
class _RouteStop:
    """A Google Routes transit stop — the {name, location} wire shape."""

    name: str = ""
    lat: float | None = None
    lon: float | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> _RouteStop:
        lat_lng = (raw.get("location") or {}).get("latLng") or {}
        return cls(name=raw.get("name", ""), lat=lat_lng.get("latitude"), lon=lat_lng.get("longitude"))


@dataclass(frozen=True)
class _RouteStopDetails:
    """A Google Routes step's stopDetails — the {departureStop, arrivalStop}
    wire shape."""

    departure_stop: _RouteStop
    arrival_stop: _RouteStop

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> _RouteStopDetails:
        return cls(
            departure_stop=_RouteStop.from_dict(raw.get("departureStop") or {}),
            arrival_stop=_RouteStop.from_dict(raw.get("arrivalStop") or {}),
        )



def _transit_legs(route: _Route) -> list[JourneyLeg]:
    """Parse a Google Routes TRANSIT route into journey legs."""
    legs: list[JourneyLeg] = []
    for leg in route.legs:
        for step in leg.steps:
            seconds = _duration_seconds(step.static_duration)
            duration = Quantity(round(Quantity(seconds, "second").to("minute").magnitude), "minute")
            travel_mode = step.travel_mode
            transit_details = step.transit_details
            if travel_mode == "WALK":
                legs.append(JourneyLeg(mode=LegMode.WALK, duration=duration))
                continue
            if travel_mode == "TRANSIT":
                line = transit_details.transit_line if transit_details else None
                mode = _transit_leg_mode(line)
                line_name = (line.name_short or line.name or "") if line else ""
                agencies = [agency.name or "" for agency in line.agencies] if line else []
                end_station = GoogleRoutesClient.NR_LONDON_TERMINUS.get(agencies[0], "") if agencies else ""
                legs.append(
                    JourneyLeg(
                        mode=mode,
                        duration=duration,
                        line_name=line_name,
                        end_station=end_station,
                    )
                )
    return legs


def _transit_leg_mode(line: _RouteTransitLine | None) -> LegMode:
    """Classify a transit leg from its line/agency (the API gates
    vehicle type).  TfL-run lines are tube when named, bus when
    numbered; anything else on the national network is a train."""
    name = (line.name_short or line.name or "").strip() if line else ""
    agencies = [agency.name or "" for agency in line.agencies] if line else []
    if any("Transport for London" in a for a in agencies):
        return LegMode.BUS if name.isdigit() else LegMode.TUBE
    return LegMode.TRAIN


def _bus_stops(route: _Route) -> tuple[TransitStop, ...]:
    """The route's bus steps as stop records — ``vehicle.type`` is what
    states a step is a bus (trains and tubes share the transit shape)."""
    stops: list[TransitStop] = []
    for leg in route.legs:
        for step in leg.steps:
            details = step.transit_details
            line = details.transit_line if details else None
            if step.travel_mode != "TRANSIT" or line is None or line.vehicle is None:
                continue
            if line.vehicle.type != "BUS":
                continue
            stops.append(_bus_stop(details))
    return tuple(stops)


def _bus_stop(details: _RouteTransitDetails | None) -> TransitStop:
    """One bus step's stops as the orchestration reads them — a step
    Google states no stopDetails for reads as unnamed and unlocated."""
    stop_details = details.stop_details if details else None
    departure = stop_details.departure_stop if stop_details else _RouteStop()
    arrival = stop_details.arrival_stop if stop_details else _RouteStop()
    return TransitStop(
        departure_name=departure.name,
        arrival_name=arrival.name,
        departure_lat=departure.lat,
        departure_lon=departure.lon,
        arrival_lat=arrival.lat,
        arrival_lon=arrival.lon,
    )


class GoogleRoutesClientLike(Protocol):
    """The orchestration seam ``GoogleRoutesClient`` satisfies — and the type
    tests stub out, so ``CommuteRouter`` never sees a real HTTP client."""

    async def post_route(
        self, *, origin: str | GeoPoint, destination: str | GeoPoint, travel_mode: str, timeout: float
    ) -> GoogleRoute | None: ...

    async def post_bus_route(self, *, origin: str, destination: str) -> BusRoute | None: ...


class GoogleRoutesClient:
    """Owns the raw Google Routes HTTP machinery: auth, caching, POST.

    Split out of ``CommuteRouter`` so the commute orchestration (walk /
    transit / drive choice) and the HTTP transport evolve independently.
    """

    GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
    HTTP_TOO_MANY_REQUESTS = 429

    # National Rail operators that serve London → their London terminus.
    # The Google Routes TRANSIT response omits stop names, so the fallback
    # journey names its last rail leg from this map — RailFareNode then finds
    # the terminal and prices the journey (LON-group fallback covers operators
    # whose per-terminal fare is missing).  Unmapped operators leave the leg
    # unnamed; the commute stays unpriced rather than carrying a guessed cost.
    NR_LONDON_TERMINUS: dict[str, str] = {
        "GWR": "London Paddington Rail Station",
        "Great Western Railway": "London Paddington Rail Station",
        "LNER": "London Kings Cross Rail Station",
        "London North Eastern Railway": "London Kings Cross Rail Station",
        "Avanti West Coast": "London Euston Rail Station",
        "South Western Railway": "London Waterloo Rail Station",
        "SWR": "London Waterloo Rail Station",
        "Southern": "London Bridge Rail Station",
        "Southeastern": "London Victoria Rail Station",
        "Thameslink": "London Blackfriars Rail Station",
        "Greater Anglia": "London Liverpool Street Rail Station",
        "Chiltern Railways": "London Marylebone Rail Station",
        "c2c": "London Fenchurch Street Rail Station",
        "East Midlands Railway": "London St Pancras International Rail Station",
    }

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

    async def post_route(
        self,
        *,
        origin: str | GeoPoint,
        destination: str | GeoPoint,
        travel_mode: str,
        timeout: float,
    ) -> GoogleRoute | None:
        """One route for the orchestration: the route's own duration and
        distance, plus its legs as journey legs when it is a transit route.

        ``None`` when the API returned no payload or the payload carries no
        route — Google's answer for a journey it will not plan.
        """
        transiting = travel_mode == "TRANSIT"
        # TRANSIT needs each step's mode, duration and line; walk and drive
        # need only the route's own totals.
        mask = (
            "routes.duration,routes.legs.steps.travelMode,"
            "routes.legs.steps.staticDuration,routes.legs.steps.transitDetails"
            if transiting
            else "routes.duration,routes.distanceMeters,routes.legs"
        )
        data = await self.post(
            _RoutesBody(
                origin=_address_waypoint(origin),
                destination=_address_waypoint(destination),
                travel_mode=travel_mode,
            ),
            mask,
            options=GoogleRoutesOptions(timeout=timeout),
        )
        if data is None:
            return None
        route = _first_route(data)
        if route is None:
            return None
        return GoogleRoute(
            duration_s=_duration_seconds(route.duration),
            distance_meters=route.distance_meters,
            legs=tuple(_transit_legs(route)) if transiting else (),
        )

    async def post_bus_route(self, *, origin: str, destination: str) -> BusRoute | None:
        """A TRANSIT route restricted to its BUS steps: every bus the
        traveler boards, as Google states its stops.

        The route is the walkable one (``less_walking``) and alternatives are
        off: the bus planner replaces a too-long walk, so a second route
        could only be discarded.  ``None`` when the API returned no payload
        or the payload carries no route.
        """
        data = await self.post(
            _RoutesBody(
                origin=_address_waypoint(origin),
                destination=_address_waypoint(destination),
                travel_mode="TRANSIT",
                transit_preferences=_TransitPreferences(routing_preference="less_walking"),
                compute_alternative_routes=False,
            ),
            "routes.duration,routes.legs",
            options=GoogleRoutesOptions(timeout=15.0),
        )
        if data is None:
            return None
        route = _first_route(data)
        if route is None:
            return None
        return BusRoute(duration_s=_duration_seconds(route.duration), stops=_bus_stops(route))




