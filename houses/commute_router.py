"""Commute routing — unified interface for walking, transit, and driving.

The caller describes the traveler; ``CommuteRouter.get_commute`` handles the rest.
No knowledge of Google, TfL, or ORS leaks to callers.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import uk_postcodes_parsing as _ukp
from money import Money
from pint import Quantity
from uk_postcodes_parsing.postcode_utils import to_outcode as _to_outcode

from dag.attempt import Attempt
from dag.guard import capture_async, guarded_async
from dag.http_error import HttpError
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.geopoint import GeoPoint
from houses.google_routes_client import GoogleRoutesClient, GoogleRoutesClientLike
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.services_provider import get_services
from houses.settings import settings

logger = logging.getLogger(__name__)

type _TiebreakKey = tuple[int, float]  # (no-cost-first flag, duration minutes)


class CommuteRouter:
    """Aggregates all commute-routing logic: walk, transit, drive, and bus fallback."""
    # Congestion-zone and Greater-London bounding boxes — coordinate data,
    # named once (the values ARE the geometry, not tunables).
    _CONGESTION_LAT_MIN = 51.5
    _CONGESTION_LAT_MAX = 51.52
    _CONGESTION_LON_MIN = -0.15
    _CONGESTION_LON_MAX = 0.01
    _LONDON_LAT_MIN = 51.3
    _LONDON_LAT_MAX = 51.7
    _LONDON_LON_MIN = -0.5
    _LONDON_LON_MAX = 0.3

    def __init__(
        self,
        *,
        routes_client: GoogleRoutesClientLike | None = None,
        google_route_fn=None,
        tfl_transit_fn=None,
        congestion_fn=None,
    ) -> None:
        """Initialise the router with optional route-planner overrides (DI).

        Each function defaults to the real implementation; tests inject
        fakes through these keyword-only parameters.
        """
        self._google_routes_client: GoogleRoutesClientLike = routes_client or GoogleRoutesClient()
        self._google_route_fn: Callable[..., Awaitable[Attempt[Commute]]] = (
            google_route_fn or self._google_route_commute
        )
        self._tfl_transit_fn: Callable[..., Awaitable[Attempt[Commute]]] = tfl_transit_fn or self._tfl_transit_commute
        self._congestion_fn: Callable[[str | GeoPoint], bool] = congestion_fn or self.in_congestion_zone

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
        """The bus node's route seam — the client's ``post_bus_route``,
        passed into BusRouteNode by the pipeline builder (tests override it
        via the services router)."""
        return self._google_routes_client.post_bus_route

    @staticmethod
    def _extract_outcode(text: str) -> str | None:
        """Extract the UK postcode outcode from a postcode or address string.

        Uses ``uk-postcodes-parsing`` to reliably find postcodes embedded
        in full addresses (e.g. ``"1 Example Street, London SW1P 1AA"``).
        Returns ``None`` for coordinate strings (``"51.5,-0.1"``).
        """
        postcodes = _ukp.parse_from_corpus(text.strip().upper(), attempt_fix=False)
        if not postcodes:
            return None
        return _to_outcode(postcodes[0].postcode)

    @staticmethod
    def in_congestion_zone(dest: str | GeoPoint) -> bool:
        if isinstance(dest, GeoPoint):
            return (
                CommuteRouter._CONGESTION_LAT_MIN
                < dest.lat
                < CommuteRouter._CONGESTION_LAT_MAX
                and CommuteRouter._CONGESTION_LON_MIN < dest.lon < CommuteRouter._CONGESTION_LON_MAX
            )
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
            return (
                CommuteRouter._LONDON_LAT_MIN < dest.lat < CommuteRouter._LONDON_LAT_MAX
                and CommuteRouter._LONDON_LON_MIN < dest.lon < CommuteRouter._LONDON_LON_MAX
            )
        oc = CommuteRouter._extract_outcode(dest)
        if oc:
            return oc.startswith(("E", "EC", "N", "NW", "SE", "SW", "W", "WC"))
        return False

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

        # A failure keeps the reason — the client appends the response body
        # to an HTTP error (e.g. "400 … LatLng cannot be specified as an
        # Address Waypoint").
        route, failure = await capture_async(
            lambda: self._google_routes_client.post_route(
                origin=origin,
                destination=dest,
                travel_mode=mode,
                timeout=15.0 if mode == "DRIVE" else 10.0,
            )
        )
        if failure is not None:
            return Attempt.impossible(f"Google Routes API request failed for {mode}: {failure}")

        if route is None:
            return self._infeasible_commute("Google Routes API returned no data")

        duration_min = round(route.duration.to("minute").magnitude)
        assert route.distance is not None, "the walk/drive field mask requests distanceMeters"

        dest_str = dest if isinstance(dest, str) else f"{dest.lat},{dest.lon}"
        if mode == "WALK":
            leg = JourneyLeg(
                mode=LegMode.WALK,
                duration=Quantity(duration_min, "minute"),
                end_station=dest_str,
            )
            daily = Money(amount="0", currency="GBP")
        else:
            distance_km = route.distance.to("km").magnitude
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
            error=(
                f"google_routes_{mode.lower()}: duration={duration_min}min "
                f"distance={route.distance.to('meter').magnitude:.0f}m"
            ),
        )

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
        route, failure = await capture_async(
            lambda: self._google_routes_client.post_route(
                origin=origin,
                destination=dest.address,
                travel_mode="TRANSIT",
                timeout=15.0,
            )
        )
        if failure is not None:
            logger.warning("Google transit fallback failed for %s: %s", origin, failure)
            return None
        if route is None or not route.legs:
            return None
        # Total from the route's own duration — the sum of per-leg
        # rounded minutes drifts (99.2 → 98 when legs round individually).
        total_min = round(route.duration.to("minute").magnitude)
        return Commute(
            person=Person(name="", has_car=False),
            label=dest.label,
            destination=dest,
            duration=Quantity(total_min, "minute"),
            daily_cost=Money(amount="0", currency="GBP"),
            mode="transit",
            _details=(CostGroup(legs=route.legs, operator="TfL", cost=None),),
        )

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
        return await guarded_async(
            lambda: (
                self._tfl_transit_fn(origin_str, dest_str, has_car, services=services)
                if services is not None
                else self._tfl_transit_fn(origin_str, dest_str, has_car)
            ),
            lambda exc: Attempt.impossible(f"tfl_transit: {exc}"),
            reraise=(httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException, HttpError),
        )


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

        def _tiebreak(c: Commute) -> _TiebreakKey:
            no_cost = 1 if c.daily_cost == Money(amount="0", currency="GBP") else 0
            return (no_cost, c.duration.magnitude or 0)

        best = min(valid_values, key=_tiebreak)
        errors = [a.error for a in candidates if not a.succeeded and a.error]
        return Attempt.succeeded(best, error="; ".join(errors) if errors else "")


