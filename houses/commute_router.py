# lucidlint: ignore bulk-suppression per-site whys are mandated (review-log scope decision 5: no config ignores)
"""Commute routing — unified interface for walking, transit, and driving.

The caller describes the traveler; ``CommuteRouter.get_commute`` handles the rest.
No knowledge of Google, TfL, or ORS leaks to callers.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx
import uk_postcodes_parsing as _ukp
from money import Money
from pint import Quantity
from uk_postcodes_parsing.postcode_utils import to_outcode as _to_outcode

from dag.attempt import Attempt
from dag.http_error import HttpError
from houses.api_cache import cached_async_client, get_cached, set_cached
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.geopoint import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.services_provider import get_services
from houses.settings import settings

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class _LatLng:
    """A Google Routes latitude/longitude pair (request wire shape)."""

    latitude: float
    longitude: float

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return dict(latitude=self.latitude, longitude=self.longitude)


@dataclass(frozen=True)
class _Waypoint:
    """A Google Routes waypoint (request wire shape)."""

    location: _LatLng | None = None
    address: str = ""

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        if self.location is not None:
            return {"location": {"latLng": self.location.to_dict()}}
        return {"address": self.address}


@runtime_checkable
class RoutesPostClient(Protocol):
    """Structural type for the transport seam tests stub out."""

    # lucidlint: ignore record-shape transport seam contract — the request body is the caller's
    # payload and the response is Google's (coding-standards.md)
    async def post(  # lucidlint: ignore record-shape the response body is Google's wire payload (coding-standards.md)
        self, body: dict, field_mask: str, *, options: GoogleRoutesOptions | None = None
    ) -> dict | None: ...


class GoogleRoutesClient:
    """Owns the raw Google Routes HTTP machinery: auth, caching, POST.

    Split out of ``CommuteRouter`` so the commute orchestration (walk /
    transit / drive choice) and the HTTP transport evolve independently.
    """

    GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

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

    # lucidlint: ignore record-shape transport seam contract — the request body is the caller's
    # payload and the response is Google's (coding-standards.md)
    async def post(  # lucidlint: ignore record-shape the response body is Google's wire payload (coding-standards.md)
        self,
        body: dict,
        field_mask: str,
        *,
        options: GoogleRoutesOptions | None = None,
    ) -> dict | None:
        """POST to Google Routes API, caching responses and direct HTTP call.

        Raises ``ValueError`` if the API key is not configured.
        """
        options = self._merge_defaults(options)
        google_key = settings.google_maps_api_key if options.api_key is None else options.api_key
        if not google_key:
            raise ValueError("Google Maps API key not configured")

        # lucidlint: ignore record-shape HTTP request headers — keyed collection, not a record (review-log)
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": google_key,
            "X-Goog-FieldMask": field_mask,
        }
        key = json.dumps(body, sort_keys=True)
        cached = get_cached("POST", self.GOOGLE_ROUTES_URL, None, key)
        if cached is not None:
            return cached

        client_factory = options.client_factory or cached_async_client
        async with client_factory(timeout=options.timeout) as client:
            resp = await client.post(self.GOOGLE_ROUTES_URL, json=body, headers=headers)
            if resp.status_code == 429:
                raise HttpError(429, "rate limited", headers=dict(resp.headers))
            self._raise_with_body(resp)
            data = resp.json()
            (options.set_cached_fn or set_cached)("POST", self.GOOGLE_ROUTES_URL, None, key, data)
            return data



# lucidlint: ignore latent-class DI surface — every method hangs off the injected route/client fns, so no
class CommuteRouter:
    """Aggregates all commute-routing logic: walk, transit, drive, and bus fallback."""

    def __init__(
        self,
        *,
        routes_client: RoutesPostClient | None = None,
        google_route_fn=None,
        tfl_transit_fn=None,
        congestion_fn=None,
    ) -> None:
        """Initialise the router with optional route-planner overrides (DI).

        Each function defaults to the real implementation; tests inject
        fakes through these keyword-only parameters.
        """
        self._google_routes_client: RoutesPostClient = routes_client or GoogleRoutesClient()
        self._google_route_fn: Callable[..., Awaitable[Attempt[Commute]]] = (
            google_route_fn or self._google_route_commute
        )
        self._tfl_transit_fn: Callable[..., Awaitable[Attempt[Commute]]] = tfl_transit_fn or self._tfl_transit_commute
        self._congestion_fn: Callable[[str | GeoPoint], bool] = congestion_fn or self.in_congestion_zone

    GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
    ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"

    # Congestion zone — central London postcode outcodes never worth driving to
    _CONGESTION_OUTCODES: frozenset[str] = frozenset(
        {
            # EC — all EC districts are inside the zone
            "EC1A",
            "EC1N",
            "EC1R",
            "EC1V",
            "EC1Y",
            "EC2A",
            "EC2N",
            "EC2R",
            "EC2V",
            "EC2Y",
            "EC3A",
            "EC3N",
            "EC3R",
            "EC3V",
            "EC4A",
            "EC4N",
            "EC4M",
            "EC4R",
            "EC4V",
            "EC4Y",
            # WC — all WC districts are inside
            "WC1A",
            "WC1B",
            "WC1E",
            "WC1H",
            "WC1N",
            "WC1R",
            "WC1V",
            "WC1X",
            "WC2A",
            "WC2B",
            "WC2E",
            "WC2H",
            "WC2N",
            "WC2R",
            # W1 — all W1 districts are inside
            "W1A",
            "W1B",
            "W1C",
            "W1D",
            "W1F",
            "W1G",
            "W1H",
            "W1J",
            "W1K",
            "W1M",
            "W1N",
            "W1P",
            "W1R",
            "W1S",
            "W1T",
            "W1U",
            "W1V",
            "W1W",
            "W1X",
            "W1Y",
            # SW1 — all SW1 districts are inside
            "SW1A",
            "SW1E",
            "SW1H",
            "SW1P",
            "SW1V",
            "SW1W",
            "SW1X",
            "SW1Y",
            # SE1, N1, E1, E2, E14 — excluded because large parts are outside
            # the zone boundary (Bermondsey, Angel, Bethnal Green, Canary Wharf).
            # The tiebreak in get_commute() will prefer TfL transit for these
            # destinations anyway, but driving remains a valid option.
        }
    )

    @staticmethod
    def _raise_with_body(resp: httpx.Response) -> None:
        """Call ``raise_for_status()`` with the response body appended.

        httpx.HTTPStatusError.__str__() only includes the status code and
        URL, not the response body.  When Google Routes rejects a request
        (e.g. 400 with 'LatLng cannot be specified as an Address Waypoint'),
        the body is the only place the reason appears.
        """
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = resp.text[:1000]
            raise httpx.HTTPStatusError(f"{e} — {body}", request=e.request, response=e.response) from e

    @property
    def google_routes_post(self):
        """The bus node's HTTP seam — passed into BusRouteNode by the
        pipeline builder (tests override it via the services router)."""
        return self._google_routes_client.post

    @staticmethod
    def _extract_outcode(text: str) -> str | None:
        """Extract the UK postcode outcode from a postcode or address string.

        Uses ``uk-postcodes-parsing`` to reliably find postcodes embedded
        in full addresses (e.g. ``"1 Drummond Gate, London SW1V 2QQ"``).
        Returns ``None`` for coordinate strings (``"51.5,-0.1"``).
        """
        postcodes = _ukp.parse_from_corpus(text.strip().upper(), attempt_fix=False)
        if not postcodes:
            return None
        return _to_outcode(postcodes[0].postcode)

    @staticmethod
    def in_congestion_zone(dest: str | GeoPoint) -> bool:
        if isinstance(dest, GeoPoint):
            return 51.5 < dest.lat < 51.52 and -0.15 < dest.lon < 0.01
        oc = CommuteRouter._extract_outcode(dest)
        if oc:
            return oc in CommuteRouter._CONGESTION_OUTCODES
        return False

    @staticmethod
    def _is_london_area(dest: str | GeoPoint) -> bool:
        """Rough check: is this destination in the TfL service area?

        This is an OPTIMISATION, not a correctness gate.  A false positive
        (trying TfL for an out-of-area destination) is harmless — the API
        returns no routes and the caller falls through to driving.  A false
        negative means we skip TfL for a London destination, still getting
        a valid driving result.
        """
        if isinstance(dest, GeoPoint):
            return 51.3 < dest.lat < 51.7 and -0.5 < dest.lon < 0.3  # approx Greater London
        oc = CommuteRouter._extract_outcode(dest)
        if oc:
            return oc.startswith(("E", "EC", "N", "NW", "SE", "SW", "W", "WC"))
        return False

    @staticmethod
    def _parse_coord(loc: str) -> _Waypoint | None:
        """Parse a ``"lat,lon"`` string into a location waypoint, else None."""
        try:
            lat, lon = loc.split(",", 1)
            return _Waypoint(location=_LatLng(latitude=float(lat), longitude=float(lon)))
        except (ValueError, TypeError):
            return None


    @staticmethod
    def _address_waypoint(loc: str | GeoPoint) -> _Waypoint:
        """Build a Google Routes waypoint from a postcode string or GeoPoint."""
        if isinstance(loc, GeoPoint):
            return _Waypoint(location=_LatLng(latitude=loc.lat, longitude=loc.lon))
        # If the string looks like "lat,lon", parse it as a location waypoint
        if "," in loc:
            waypoint = CommuteRouter._parse_coord(loc)
            if waypoint is not None:
                return waypoint
        return _Waypoint(address=loc)

    # ------------------------------------------------------------------
    # Walking — Google Routes walking mode
    # ------------------------------------------------------------------

    @staticmethod
    def _infeasible_commute(label: str = "") -> Attempt[Commute]:
        """Return a succeeded Commute that marks a route as not viable.

        Callers should check the ``infeasible`` flag before accessing route
        details.  Duration is zero so downstream filters (``duration > 0``)
        naturally exclude this commute.
        """
        return Attempt.succeeded(
            Commute(
                person=Person(name="", has_car=False),
                label=label,
                destination=PlaceOfInterest(label="", address=""),
                duration=Quantity(0, "minute"),
                daily_cost=Money(amount="0", currency="GBP"),
                mode="",
                _details=(),
                infeasible=True,
            )
        )

    async def _google_route_commute(
        self,
        origin: str | GeoPoint,
        dest: str | GeoPoint,
        mode: str,
        max_walk_minutes: int | None = None,
    ) -> Attempt[Commute]:
        """Try walking or driving via Google Routes API.

        Skips the API call entirely when the straight-line distance makes
        walking infeasible (exceeds ``max_walk_minutes`` at 5 km/h).

        Returns:
            Attempt.succeeded(Commute) with infeasible=True when no route
            is available (distance too far, API returned no routes).
            Attempt.impossible for HTTP/network errors and exceptions.
        """
        if mode == "WALK" and max_walk_minutes is not None:
            max_walk_km = max_walk_minutes * 5.0 / 60.0  # 5 km/h walking pace
            if isinstance(origin, GeoPoint) and isinstance(dest, GeoPoint):
                dist_km = origin.distance_km_to(dest)
                if dist_km > max_walk_km:
                    return self._infeasible_commute(
                        f"straight-line distance {dist_km:.1f} km exceeds {max_walk_km:.1f} km"
                    )

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        body = {
            "origin": self._address_waypoint(origin),
            "destination": self._address_waypoint(dest),
            "travelMode": mode,
        }
        mask = "routes.duration,routes.distanceMeters,routes.legs"
        try:
            data = await self._google_routes_client.post(
                body, mask, options=GoogleRoutesOptions(timeout=15.0 if mode == "DRIVE" else 10.0)
            )
        # lucidlint: ignore broad-except Google Routes failure → Attempt.impossible with error + body
        except Exception as e:
            # Keep the reason — _raise_with_body appends the response body
            # (e.g. "400 … LatLng cannot be specified as an Address Waypoint").
            return Attempt.impossible(f"Google Routes API request failed for {mode}: {e}")

        if data is None:
            return self._infeasible_commute("Google Routes API returned no data")

        routes = data.get("routes", [])
        if not routes:
            return self._infeasible_commute("Google Routes returned no routes")

        duration_sec = int(routes[0].get("duration", "0s").rstrip("s"))
        duration_min = round(duration_sec / 60)
        distance_meters = routes[0].get("distanceMeters", 0)

        dest_str = dest if isinstance(dest, str) else f"{dest.lat},{dest.lon}"
        if mode == "WALK":
            leg = JourneyLeg(
                mode=LegMode.WALK,
                duration=Quantity(duration_min, "minute"),
                end_station=dest_str,
            )
            daily = Money(amount="0", currency="GBP")
        else:
            distance_km = distance_meters / 1000
            leg = JourneyLeg(
                mode=LegMode.DRIVE,
                duration=Quantity(duration_min, "minute"),
                distance=Quantity(distance_km, "km"),
                end_station=dest_str,
            )
            daily = Money(amount="0", currency="GBP")
        return Attempt.succeeded(
            Commute(
                person=Person(name="", has_car=False),
                label="",
                destination=PlaceOfInterest(label="", address=dest_str),
                duration=Quantity(duration_min, "minute"),
                daily_cost=daily or Money(amount="0", currency="GBP"),
                mode="walk" if mode == "WALK" else "drive",
                _details=(
                    CostGroup(
                        legs=(leg,),
                        cost=daily,
                    ),
                ),
            ),
            error=f"google_routes_{mode.lower()}: duration={duration_min}min distance={distance_meters}m",
        )
    # National Rail operators that serve London → their London terminus.
    # The Google Routes TRANSIT response omits stop names, so the
    # fallback journey names its last rail leg from this map — RailFareNode
    # then finds the terminal and prices the journey (LON-group fallback
    # covers operators whose per-terminal fare is missing).  Unmapped
    # operators leave the leg unnamed; the commute stays unpriced rather
    # than carrying a guessed cost.
    _NR_LONDON_TERMINUS: dict[str, str] = {
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

    async def transit_route(self, origin: GeoPoint, dest: PlaceOfInterest | str) -> Commute | None:
        """National Rail fallback routing via Google Routes TRANSIT.

        Called by TransitNode when TfL has no route for the origin (its
        planner's coverage ends west of Newbury).  Returns a feasible
        commute with the transit legs Google found (walk → GWR train →
        tube/bus → walk); the fare is priced downstream by RailFareNode
        from the property's nearest station to the London terminus.
        ``dest`` is the destination address string or POI.  Returns None
        when unroutable or on failure — the caller keeps its
        succeeded-infeasible result so the commute selector still falls
        back to drive/walk.
        """
        if isinstance(dest, str):
            dest = PlaceOfInterest(label="", address=dest)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        body = {
            "origin": self._address_waypoint(origin),
            "destination": self._address_waypoint(dest.address),
            "travelMode": "TRANSIT",
        }
        mask = (
            "routes.duration,routes.legs.steps.travelMode,"
            "routes.legs.steps.staticDuration,routes.legs.steps.transitDetails"
        )
        try:
            data = await self._google_routes_client.post(
                body, mask, options=GoogleRoutesOptions(timeout=15.0)
            )
        # lucidlint: ignore broad-except Google Routes failure → None, caller keeps infeasible
        except Exception as e:
            logger.warning("Google transit fallback failed for %s: %s", origin, e)
            return None
        if not data:
            return None
        routes = data.get("routes") or []
        if not routes:
            return None
        legs = self._transit_legs(routes[0])
        if not legs:
            return None
        # Total from the route's own duration — the sum of per-leg
        # rounded minutes drifts (99.2 → 98 when legs round individually).
        total_min = round(int(str(routes[0].get("duration", "0s")).rstrip("s")) / 60)
        return Commute(
            person=Person(name="", has_car=False),
            label=dest.label,
            destination=dest,
            duration=Quantity(total_min, "minute"),
            daily_cost=Money(amount="0", currency="GBP"),
            mode="transit",
            _details=(CostGroup(legs=tuple(legs), operator="TfL", cost=None),),
        )

    def _transit_legs(self, route: dict[str, Any]) -> list[JourneyLeg]:
        """Parse a Google Routes TRANSIT route into journey legs."""
        legs: list[JourneyLeg] = []
        for leg in route.get("legs") or []:
            for step in leg.get("steps") or []:
                duration = Quantity(round(int(str(step.get("staticDuration", "0s")).rstrip("s")) / 60), "minute")
                travel_mode = step.get("travelMode", "")
                transit_details = step.get("transitDetails") or {}
                line = transit_details.get("transitLine") or {}
                if travel_mode == "WALK":
                    legs.append(JourneyLeg(mode=LegMode.WALK, duration=duration))
                    continue
                if travel_mode == "TRANSIT":
                    mode = self._transit_leg_mode(line)
                    line_name = line.get("nameShort") or line.get("name") or ""
                    agencies = [a.get("name") or "" for a in line.get("agencies") or []]
                    end_station = self._NR_LONDON_TERMINUS.get(agencies[0], "") if agencies else ""
                    legs.append(
                        JourneyLeg(
                            mode=mode,
                            duration=duration,
                            line_name=line_name,
                            end_station=end_station,
                        )
                    )
        return legs

    @staticmethod
    def _transit_leg_mode(line: dict[str, Any]) -> LegMode:
        """Classify a transit leg from its line/agency (the API gates
        vehicle type).  TfL-run lines are tube when named, bus when
        numbered; anything else on the national network is a train."""
        name = (line.get("nameShort") or line.get("name") or "").strip()
        agencies = [a.get("name") or "" for a in line.get("agencies") or []]
        if any("Transport for London" in a for a in agencies):
            return LegMode.BUS if name.isdigit() else LegMode.TUBE
        return LegMode.TRAIN

    # ------------------------------------------------------------------
    # Transit — TfL via TflClient (London area)
    # ------------------------------------------------------------------


    async def _tfl_transit_commute(
        self,
        origin_postcode: str,
        dest_postcode: str,
        has_car: bool,
        *,
        services: Any | None = None,
    ) -> Attempt[Commute]:
        """Transit routing via TfL API.

        Tries routes with and without bus mode, picks the best.
        Also applies bus fallback via Google Routes when the first-leg
        walk exceeds max_walk_minutes (TfL doesn't cover some areas).
        """
        client_factory = (services or get_services()).tfl_client_factory
        label = dest_postcode
        no_bus = await client_factory(
            origin_postcode,
            dest_postcode,
            label,
            park_and_ride=has_car,
        ).plan()

        # When the traveler has a car, park-and-ride is preferred over bus.
        # If no_bus succeeded, return it directly.  If it failed, fall through
        # to try with_bus as a last resort.
        if has_car and not no_bus.impossible:
            return Attempt.succeeded(no_bus.value_or_none(), error=no_bus.error)

        with_bus = await client_factory(
            origin_postcode, dest_postcode, label, park_and_ride=has_car, allow_bus=True
        ).plan()

        if no_bus.impossible and with_bus.impossible:
            errors = [e for e in (no_bus.error, with_bus.error) if e]
            return Attempt.impossible("; ".join(errors) if errors else "no transit route available")

        empty = Commute(
            person=Person(name="", has_car=has_car),
            label=label,
            destination=PlaceOfInterest(label=label, address=dest_postcode),
            duration=Quantity(0, "minute"),
            daily_cost=Money(amount="0", currency="GBP"),
        )
        no_bus_val = no_bus.value_or(empty)
        with_bus_val = with_bus.value_or(empty)
        result = self._pick_best_route(no_bus_val, with_bus_val)
        return Attempt.succeeded(
            result,
            error=f"tfl_transit: duration={result.duration.magnitude}min mode={result.mode}",
        )

    @staticmethod
    def _pick_best_route(a: Commute, b: Commute) -> Commute:
        """Pick the better of two transit route options.

        Uses the ``b`` result only if it saves at least
        ``bus_walk_penalty_minutes`` over ``a``.
        """
        if b.duration.magnitude == 0:
            return a
        if a.duration.magnitude == 0:
            return b
        savings = a.duration.magnitude - b.duration.magnitude
        if savings >= settings.bus_walk_penalty.magnitude:
            return b
        return a

    async def _tfl_transit_attempt(
        self,
        origin_str: str,
        dest_str: str,
        has_car: bool,
        *,
        services: Any | None = None,
    ) -> Attempt[Commute]:
        """Run the TfL transit planner, converting unexpected failures into an
        impossible Attempt (HTTP-level errors are re-raised for the DAG retry)."""
        try:
            if services is not None:
                return await self._tfl_transit_fn(origin_str, dest_str, has_car, services=services)
            return await self._tfl_transit_fn(origin_str, dest_str, has_car)
        except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException, HttpError):
            raise
        # lucidlint: ignore broad-except TfL transit failure → Attempt.impossible; HTTP-class errors re-raise above
        except Exception as e:
            return Attempt.impossible(f"tfl_transit: {e}")


    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_commute(
        self,
        origin: str | GeoPoint,
        dest: str | GeoPoint,
        *,
        has_car: bool,
        max_walk_minutes: int | None = None,
        services: Any | None = None,
    ) -> Attempt[Commute]:
        """Route from origin to destination based on the traveler's circumstances.

        Tries walking first (cheapest), then transit (London only),
        then driving (if car available and not congestion zone).
        Returns the best non-zero-duration result with preference
        for priced routes (TfL has real cost data).

        Each mode returns an ``Attempt[Commute]`` — errors from failed
        modes are carried in the Attempt's ``error`` field, visible
        in the DAG provenance chain.
        """

        dest_str = dest if isinstance(dest, str) else f"{dest.lat},{dest.lon}"
        dest_in_congestion = self._congestion_fn(dest)

        walk_attempt = await self._google_route_fn(origin, dest, "WALK", max_walk_minutes)
        if max_walk_minutes is not None and walk_attempt.succeeded:
            walk_value = walk_attempt.value_or_none()
            if walk_value is not None and walk_value.duration.magnitude <= max_walk_minutes:
                return walk_attempt

        candidates: list[Attempt[Commute]] = [walk_attempt]
        if self._is_london_area(dest):
            origin_str = origin if isinstance(origin, str) else f"{origin.lat},{origin.lon}"
            tfl_attempt = await self._tfl_transit_attempt(origin_str, dest_str, has_car, services=services)
            candidates.append(tfl_attempt)
        else:
            candidates.append(Attempt.impossible("not in London area"))

        if has_car and not dest_in_congestion:
            drive_attempt = await self._google_route_fn(origin, dest, "DRIVE")
            candidates.append(drive_attempt)

        valid: list[Attempt[Commute]] = []
        valid_values: list[Commute] = []
        for a in candidates:
            c = a.value_or_none()
            if a.succeeded and c is not None and not c.infeasible and c.duration.magnitude > 0:
                valid.append(a)
                valid_values.append(c)
        if not valid:
            errors = [a.error for a in candidates if a.error]
            return Attempt.impossible("; ".join(errors) if errors else "no route available")

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        def _tiebreak(c: Commute) -> tuple[int, float]:
            no_cost = 1 if c.daily_cost == Money(amount="0", currency="GBP") else 0
            return (no_cost, c.duration.magnitude or 0)

        best = min(valid_values, key=_tiebreak)
        errors = [a.error for a in candidates if not a.succeeded and a.error]
        return Attempt.succeeded(best, error="; ".join(errors) if errors else "")


