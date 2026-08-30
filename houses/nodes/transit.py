from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, override

from money import Money
from pint import Quantity

from dag.attempt import Attempt, AttemptError, Provenance
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.commute import LegMode
from houses.commute_router import CommuteRouter as _CommuteRouter
from houses.geopoint import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.services_provider import get_services
from houses.tfl_client import TflRouteOptions


def _with_destination(
    result: Attempt[Commute],
    poi: PlaceOfInterest | None,
) -> Attempt[Commute]:
    """Patch the full destination POI (label + trips/weeks) onto a route
    result — the route planners only know the address, so without this
    the provenance would lose the destination entirely."""
    if poi is None or not result.succeeded:
        return result
    val = result.value_or_none()
# lucidlint: ignore special-case sentinel handling is the contract here
    if val is None:
        return result
    return Attempt.succeeded(replace(val, destination=poi))


def _infeasible_commute(label: str = "", reason: str = "") -> Attempt[Commute]:
    """Return a succeeded Commute that marks a route as not viable.

    ``reason`` explains WHY no route exists (missing destination address,
    TfL 404 no-journey, no car) — it travels on the Commute so the
    provenance can show it without the DAG swallowing the answer.
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
            no_route_reason=reason,
        )
    )


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommuteLeg:
    """One segment of a commute, carrying TfL line / route name where available."""

    mode: str  # walk, bus, tube, train, dlr, overground, drive, cycle, park
    duration: Quantity
    line_name: str = ""  # e.g. "Bakerloo", "Great Western Railway"
    destination: str = ""  # e.g. "Oxford Circus", "Paddington"
    cost: Money | None = None  # attributed cost (parking fees, etc.)
    operator: str = ""  # operator name for cost-bearing legs, e.g. "ParkCo"


@dataclass(frozen=True)
class CommuteResult:
    duration: Quantity
    daily_cost: Money | None = None
    label: str = ""
    mode: str = "transit"
    details: tuple[CommuteLeg, ...] = ()
    route_description: str = ""
    is_child: bool = False
    source_url: str = ""
    destination_url: str = ""


_LEG_MODE_LABEL = {
    "walk": "Walk",
    "bus": "Bus",
    "tube": "Tube",
    "train": "Train",
    "dlr": "DLR",
    "overground": "Overground",
    "tram": "Tram",
    "drive": "Drive",
    "cycle": "Cycle",
    "park": "Park",
}


def _build_details(commute: Commute) -> tuple[CommuteLeg, ...]:
    """Convert a Commute's cost groups into CommuteLeg tuples.

    Each CostGroup may carry a cost (parking fees, etc.) and an operator
    name; these are attached to the first leg in the group.
    """
    legs: list[CommuteLeg] = []
    for cg in commute.details:
        cg_cost: Money | None = cg.cost
        for i, leg in enumerate(cg.legs):
            mode_name = leg.mode.name.lower() if hasattr(leg.mode, "name") else str(leg.mode)
            legs.append(
                CommuteLeg(
                    mode=mode_name,
                    duration=leg.duration,
                    line_name=leg.line_name,
                    destination=leg.end_station,
                    cost=cg_cost if i == 0 else None,
                    operator=cg.operator if i == 0 else "",
                )
            )
    return tuple(legs)


def _route_description(legs: tuple[CommuteLeg, ...]) -> str:
    parts = []
    for leg in legs:
        label = _LEG_MODE_LABEL.get(leg.mode, leg.mode)
        dur = f"{int(leg.duration.magnitude)}m"
        part = f"{label} {dur}"
        if leg.line_name:
            part += f" ({leg.line_name})"
        if leg.destination:
            part += f" to {leg.destination}"
        parts.append(part)
    return " → ".join(parts)


@dataclass(frozen=True)
class RouteOptions:
    """Wiring for the single-mode route nodes (``WalkNode``, ``DriveNode``).

    ``route_fn`` is the DI seam defaulting to the Services route planner;
    ``max_walk`` applies to walking, ``has_car`` to driving.
    """

    best_location: Node
    poi: Node
    route_fn: Callable | None = None
    poi_info: PlaceOfInterest | None = None
    max_walk: int = 30
    has_car: bool = False


@dataclass(frozen=True)
class TransitOptions:
    """Wiring for the TfL transit family (``TflTransitNode``, ``TransitNode``).

    Both nodes read the shared fields; ``TflTransitNode`` also reads
    ``allow_bus``/``client_factory`` and ``TransitNode`` reads
    ``best_address``/``no_bus_node``/``with_bus_node``.
    """

    best_location: Node
    poi: Node
    has_car: bool = False
    poi_info: PlaceOfInterest | None = None
    allow_bus: bool = False
    client_factory: Callable[..., Any] | None = None
    best_address: Node | None = None
    no_bus_node: Node | None = None
    with_bus_node: Node | None = None
    # National Rail fallback seam: called when both TfL variants are
    # succeeded-infeasible (origin beyond TfL coverage).  Returns a
    # feasible Commute or None.  Wired to the Google Routes TRANSIT
    # router in the pipeline builder; None keeps the infeasible result
    # so the commute selector still falls back to drive/walk.
    transit_route_fn: Callable[[GeoPoint, PlaceOfInterest], Awaitable[Commute | None]] | None = None


class PersonMaxWalkNode(DerivedNode[int]):
    """The person's own walking tolerance (bus_walk_penalty minutes).

    Reads the person from the settings persons node by name. Route
    PLANNING nodes (WalkNode/TflTransitNode) deliberately do NOT depend
    on it — their routes don't change with the tolerance, and a what-if
    must never re-plan. Only the gate/selection nodes depend on it, so
    the incremental evaluator re-scores the same planned routes.
    """

    def __init__(self, node_id: str, *, persons_source, person_name: str):
        self._person_name = person_name
        super().__init__(node_id, int, (persons_source,))
        self.display_name = "Max walk"

    @override
    def compute(self, persons: Attempt[list]) -> Attempt[int]:
        if not persons.succeeded:
            return Attempt.impossible(persons.error)
        for p in persons.value_or_none() or []:
            if getattr(p, "name", None) == self._person_name:
                penalty = getattr(p, "bus_walk_penalty", None)
                if penalty is not None:
                    return Attempt.succeeded(int(penalty.magnitude))
                return Attempt.succeeded(30)
        return Attempt.succeeded(30)


class WalkLegCheckNode(DerivedNode[bool]):
    def __init__(self, node_id: str, *, transit_node, max_walk: int = 30):
        super().__init__(node_id, bool, (transit_node,))
        self._max_walk = max_walk

    @override
    def compute(self, transit: Attempt[Commute]) -> Attempt[bool]:
        if not transit.succeeded:
            return Attempt.succeeded(value=False)
        val = transit.value_or_none()
        if val is None:
            return Attempt.succeeded(value=False)
        if val.details and val.details[0].legs:
            first_leg = val.details[0].legs[0]
            walk_time = int(first_leg.duration.magnitude) if first_leg.mode == LegMode.WALK else 0
        else:
            walk_time = 0
        return Attempt.succeeded(walk_time > self._max_walk)


class WalkNode(DerivedNode[Commute]):
    """Walking commute — uses the route service from Services DI."""

    def __init__(
        self,
        node_id: str,
        *,
        options: RouteOptions,
    ):
        super().__init__(node_id, Commute, (options.best_location, options.poi))
        self.display_name = "Walk"
        self._max_walk = options.max_walk
        self._route_fn = options.route_fn
        # The full destination POI (label + trips/weeks) — the route
        # planner only knows the address, so the provenance would lose
        # the destination without this patch.
        self._poi_info = options.poi_info

    @override
    async def compute(self, location: Attempt[GeoPoint], poi: Attempt[PlaceOfInterest]) -> Attempt[Commute]:
        loc = location.value_or_none()
        poi_val = poi.value_or_none()
        if loc is None or not poi_val:
            return Attempt.impossible("missing location or destination")
        dest = poi_val.address if isinstance(poi_val, PlaceOfInterest) else (poi_val or "")
        if not dest:
            return _infeasible_commute(label="empty destination", reason="No destination address for this journey")
        if self._route_fn is not None:
            result = await self._route_fn(loc, dest, self._max_walk)
        else:

            result = await get_services().route_planner.walk_route(loc, dest, self._max_walk)
        return _with_destination(result, self._poi_info)


class DriveNode(DerivedNode[Commute]):
    """Driving commute — uses the route service from Services DI."""

    def __init__(
        self,
        node_id: str,
        *,
        options: RouteOptions,
    ):
        super().__init__(node_id, Commute, (options.best_location, options.poi))
        self.display_name = "Drive"
        self._has_car = options.has_car
        self._route_fn = options.route_fn
        self._poi_info = options.poi_info

    @override
    async def compute(self, location: Attempt[GeoPoint], poi: Attempt[PlaceOfInterest]) -> Attempt[Commute]:
        if not self._has_car:
            return _infeasible_commute(label="no car available", reason="no car available")
        loc = location.value_or_none()
        poi_val = poi.value_or_none()
        if loc is None or not poi_val:
            return Attempt.impossible("missing location or destination")
        dest = poi_val.address if isinstance(poi_val, PlaceOfInterest) else (poi_val or "")
        if not dest:
            return _infeasible_commute(label="empty destination", reason="No destination address for this journey")
        if self._route_fn is not None:
            result = await self._route_fn(loc, dest)
        else:

            result = await get_services().route_planner.drive_route(loc, dest)
        return _with_destination(result, self._poi_info)


class TflTransitNode(DerivedNode[Commute]):
    """Call TfL API for a single transit mode (with or without bus).

    One instance per ``allow_bus`` flag.  ``TransitNode`` depends on two
    of these and picks the best result.
    """

    def __init__(
        self,
        node_id: str,
        *,
        options: TransitOptions,
    ):
        super().__init__(node_id, Commute, (options.best_location, options.poi))
        self._has_car = options.has_car
        self._allow_bus = options.allow_bus
        self._poi_info = options.poi_info
        self._client_factory = options.client_factory
        self._last_no_route_detail: str = ""
        self.display_name = "TfL"

    @override
    async def compute(self, location: Attempt[GeoPoint], poi: Attempt[PlaceOfInterest]) -> Attempt[Commute]:
        # Reset first: a raising plan() (409/5xx) must not leave the
        # previous run's 404 detail to mislabel the provenance.
        self._last_no_route_detail = ""
        loc = location.value_or_none()
        poi_val = poi.value_or_none()
        if loc is None or not poi_val:
            return Attempt.impossible("missing location or destination")
        dest = poi_val.address if isinstance(poi_val, PlaceOfInterest) else (poi_val or "")
        if not dest:
            return _infeasible_commute(label="empty destination", reason="No destination address for this journey")

        origin_str = loc if isinstance(loc, str) else f"{loc.lat},{loc.lon}"
        dest_str = dest if isinstance(dest, str) else f"{dest.lat},{dest.lon}"

        client_factory = self._client_factory or get_services().tfl_client_factory
        client = client_factory(
            origin_str,
            dest_str,
            poi_val.label if isinstance(poi_val, PlaceOfInterest) else "",
            TflRouteOptions(
                park_and_ride=self._has_car,
                allow_bus=self._allow_bus,
            ),
        )
        # Dispatch the override DIRECTLY — a fake client factory supplies
        # the canned plan wholesale (DI per docs/testing-standards).
        if client._plan_override is not None:
            result = await client._plan_override(client)
        else:
            result = await client.plan()
        self._last_no_route_detail = client._no_route_detail
        return _with_destination(result, self._poi_info)

    @override
    async def build_provenance(self) -> Provenance:
        p = await super().build_provenance()
        v = self._attempt.value_or_none()
        # Only a succeeded-infeasible result carries the no-route note —
        # an impossible attempt (outage/retry-exhausted) must show its
        # own error, never a stale 404 detail.
        if self._last_no_route_detail and self._attempt.succeeded and v is not None and v.infeasible:
            p.description = f"{v.no_route_reason} ({self._last_no_route_detail})"
        return p


class TransitNode(DerivedNode[Commute]):
    """Select the best TfL transit route from two TflTransitNode results.

    Tries TfL with and without bus mode, returns the better result.
    Falls back through driving/walking via the CommuteSelectorNode
    when both TfL options are impossible.
    """

    def __init__(
        self,
        node_id: str,
        *,
        options: TransitOptions,
    ):
        if options.no_bus_node is None or options.with_bus_node is None:
            raise ValueError(f"{node_id}: TransitOptions requires no_bus_node and with_bus_node")
        deps: tuple[Node, ...] = (
            options.best_location,
            options.poi,
            options.no_bus_node,
            options.with_bus_node,
        )
        if options.best_address is not None:
            deps = deps + (options.best_address,)
        super().__init__(node_id, Commute, deps)
        self.display_name = "TfL API"
        self._has_car = options.has_car
        self._best_address = options.best_address
        self._poi_info = options.poi_info
        self._transit_route_fn = options.transit_route_fn
        self._last_fallback_detail: str | None = None

    @override
    async def compute(
        self,
        location: Attempt[GeoPoint],
        poi: Attempt[PlaceOfInterest],
        no_bus: Attempt[Commute],
        with_bus: Attempt[Commute],
        best_address: Attempt[str] | None = None,
    ) -> Attempt[Commute]:
        # Provenance narrates only the run that actually happened: a
        # fallback used by an earlier compute must not leak into a later
        # plain-TfL success (PR #68 review).
        self._last_fallback_detail = None
        no_bus_val = no_bus.value_or_none()
        with_bus_val = with_bus.value_or_none()
        if self._has_car and no_bus_val is not None and not no_bus_val.infeasible:
            best_val = no_bus_val
        elif no_bus_val is None and with_bus_val is None:
            errors = [e for e in (no_bus.error, with_bus.error) if e]
            message = "; ".join(errors) if errors else "no transit route available"
            # Keep the raw client errors in the internal message (logs),
            # but never surface them: display_message resolves to this
            # friendly user_message (walkthrough run 3 — a raw TfL 404
            # blob was rendered to the user).
            return Attempt.impossible(
                message,
                error_info=AttemptError(
                    code="no_data",
                    message=message,
                    user_message="Couldn't find a route to this destination — check the address.",
                ),
            )
        else:
            empty = Commute(
                person=Person(name="", has_car=self._has_car),
                label="",
                destination=PlaceOfInterest(label="", address=""),
                duration=Quantity(0, "minute"),
                daily_cost=Money(amount="0", currency="GBP"),
            )
            best_val = _CommuteRouter._pick_best_route(no_bus_val or empty, with_bus_val or empty)

        val = best_val
        if val is None:
            return Attempt.impossible("transit returned empty result")
        if val.infeasible:
            # No TfL transit route — succeeded-infeasible so the selector
            # can fall back to drive/walk.  When a transit_route_fn is
            # wired (Google Routes TRANSIT), try the National Rail
            # fallback first: TfL's planner has a coverage boundary west
            # of Newbury, and the registry has a station + fare for those
            # origins.  Never touch .details on an infeasible commute
            # (the accessor raises).
            fallback = await self._nr_fallback(location, poi)
            if fallback is not None:
                # Same label/destination fixups as the normal path — the
                # router only knows the address; the summary/provenance
                # must show the POI label + trips (PR #68 review).
                parts = self._id.split("/")
                label = parts[2] if len(parts) >= 3 else (fallback.destination.label or "")
                fallback = replace(fallback, label=label)
                if self._poi_info is not None:
                    fallback = replace(fallback, destination=self._poi_info)
                return Attempt.succeeded(fallback)
            return Attempt.succeeded(val)

        parts = self._id.split("/")
        label = parts[2] if len(parts) >= 3 else (val.destination.label or "")
        raw_mode = val.mode if hasattr(val, "mode") else "transit"
        mode = raw_mode.name.lower() if isinstance(raw_mode, Enum) else str(raw_mode)
        if val.details and all(leg.mode.name.lower() == "walk" for cg in val.details for leg in cg.legs):
            mode = "walk"
        daily_cost = val.daily_cost or Money("0", "GBP")

        result = Commute(
            person=Person(name="", has_car=self._has_car),
            label=label,
            destination=PlaceOfInterest(label=label, address=val.destination.address),
            duration=val.duration,
            daily_cost=daily_cost,
            mode=mode,
            _details=val.details,
        )
        if self._poi_info is not None:
            result = replace(result, destination=self._poi_info)
        return Attempt.succeeded(result)
    async def _nr_fallback(
        self,
        location: Attempt[GeoPoint],
        poi: Attempt[PlaceOfInterest],
    ) -> Commute | None:
        """National Rail fallback for origins beyond TfL coverage.

        Calls the wired transit_route_fn (Google Routes TRANSIT) with the
        property's location and the destination POI.  Returns the Commute
        on success, None when unwired, unroutable, or failed — the
        caller keeps the succeeded-infeasible result, so the commute
        selector still falls back to drive/walk.
        """
        if self._transit_route_fn is None:
            return None
        location_val = location.value_or_none()
        poi_val = poi.value_or_none()
        if location_val is None or poi_val is None:
            return None
        # The pipeline's poi input is a UserInputNode[str] holding the
        # postcode/address — only school POIs arrive as PlaceOfInterest.
        # Normalize both to a PlaceOfInterest for the router seam.
        if isinstance(poi_val, str):
            poi_val = PlaceOfInterest(label="", address=poi_val)
        elif not isinstance(poi_val, PlaceOfInterest):
            return None
        try:
            fallback = await self._transit_route_fn(location_val, poi_val)
        except Exception as e:  # lucidlint: ignore broad-except — the fallback must never mask drive/walk
            logging.getLogger(__name__).warning("National Rail fallback failed: %s", e)
            self._last_fallback_detail = f"National Rail fallback failed: {e}"
            return None
        if fallback is None or fallback.infeasible:
            return None
        self._last_fallback_detail = (
            "TfL found no route for this journey — National Rail fallback (Google transit) used"
        )
        return fallback

    @override
    async def build_provenance(self) -> Provenance:
        p = await super().build_provenance()
        if self._last_fallback_detail is not None:
            p.description = self._last_fallback_detail
        return p
