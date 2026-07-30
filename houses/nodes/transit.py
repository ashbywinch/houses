from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.commute import LegMode
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.routing import CommuteRouter as _CommuteRouter


def _infeasible_commute(label: str = "") -> Attempt[Commute]:
    """Return a succeeded Commute that marks a route as not viable."""
    return Attempt.succeeded(
        Commute(
            person=Person(name="", has_car=False),
            label=label,
            destination=PlaceOfInterest(label="", address=""),
            duration=Quantity(0, "minute"),
            daily_cost=Money("0", "GBP"),
            mode="",
            _details=(),
            infeasible=True,
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


class WalkLegCheckNode(DerivedNode[bool]):
    def __init__(self, node_id: str, *, transit_node, max_walk: int = 30):
        super().__init__(node_id, bool, (transit_node,))
        self._max_walk = max_walk

    def compute(self, transit: Attempt[Commute]) -> Attempt[bool]:
        if not transit.succeeded:
            return Attempt.succeeded(False)
        val = transit.value_or_none()
        if val is None:
            return Attempt.succeeded(False)
        if val.details and val.details[0].legs:
            first_leg = val.details[0].legs[0]
            walk_time = int(first_leg.duration.magnitude) if first_leg.mode == LegMode.WALK else 0
        else:
            walk_time = 0
        return Attempt.succeeded(walk_time > self._max_walk)


class WalkNode(DerivedNode[Commute]):
    """Walking commute — uses the route service from Services DI."""

    def __init__(self, node_id: str, *, best_location, poi, max_walk: int, route_fn=None):
        super().__init__(node_id, Commute, (best_location, poi))
        self.display_name = "Walk"
        self._max_walk = max_walk
        self._route_fn = route_fn

    async def compute(self, location: Attempt[GeoPoint], poi: Attempt[PlaceOfInterest]) -> Attempt[Commute]:
        loc = location.value_or_none()
        poi_val = poi.value_or_none()
        if loc is None or not poi_val:
            return Attempt.impossible("missing location or destination")
        dest = poi_val.address if isinstance(poi_val, PlaceOfInterest) else (poi_val or "")
        if not dest:
            return _infeasible_commute("empty destination")
        if self._route_fn is not None:
            return await self._route_fn(loc, dest, self._max_walk)
        from houses.services_provider import get_services

        return await get_services().route_planner.walk_route(loc, dest, self._max_walk)


class DriveNode(DerivedNode[Commute]):
    """Driving commute — uses the route service from Services DI."""

    def __init__(self, node_id: str, *, best_location, poi, has_car: bool, route_fn=None):
        super().__init__(node_id, Commute, (best_location, poi))
        self.display_name = "Drive"
        self._has_car = has_car
        self._route_fn = route_fn

    async def compute(self, location: Attempt[GeoPoint], poi: Attempt[PlaceOfInterest]) -> Attempt[Commute]:
        if not self._has_car:
            return _infeasible_commute("no car available")
        loc = location.value_or_none()
        poi_val = poi.value_or_none()
        if loc is None or not poi_val:
            return Attempt.impossible("missing location or destination")
        dest = poi_val.address if isinstance(poi_val, PlaceOfInterest) else (poi_val or "")
        if not dest:
            return _infeasible_commute("empty destination")
        if self._route_fn is not None:
            return await self._route_fn(loc, dest)
        from houses.services_provider import get_services

        return await get_services().route_planner.drive_route(loc, dest)


class TflTransitNode(DerivedNode[Commute]):
    """Call TfL API for a single transit mode (with or without bus).

    One instance per ``allow_bus`` flag.  ``TransitNode`` depends on two
    of these and picks the best result.
    """

    def __init__(self, node_id: str, *, best_location: Node, poi: Node, has_car: bool, allow_bus: bool = False):
        super().__init__(node_id, Commute, (best_location, poi))
        self._has_car = has_car
        self._allow_bus = allow_bus
        self.display_name = "TfL"

    async def compute(self, location: Attempt[GeoPoint], poi: Attempt[PlaceOfInterest]) -> Attempt[Commute]:
        loc = location.value_or_none()
        poi_val = poi.value_or_none()
        if loc is None or not poi_val:
            return Attempt.impossible("missing location or destination")
        dest = poi_val.address if isinstance(poi_val, PlaceOfInterest) else (poi_val or "")
        if not dest:
            return Attempt.impossible("empty destination")

        origin_str = loc if isinstance(loc, str) else f"{loc.lat},{loc.lon}"
        dest_str = dest if isinstance(dest, str) else f"{dest.lat},{dest.lon}"

        from houses.tfl_client import TflClient

        return await TflClient(
            origin_str,
            dest_str,
            poi_val.label if isinstance(poi_val, PlaceOfInterest) else "",
            park_and_ride=self._has_car,
            allow_bus=self._allow_bus,
        ).plan()


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
        best_location,
        poi,
        has_car: bool,
        max_walk: int,
        best_address=None,
        no_bus_node: TflTransitNode,
        with_bus_node: TflTransitNode,
    ):
        deps: tuple[Node, ...] = (best_location, poi, no_bus_node, with_bus_node)
        if best_address is not None:
            deps = deps + (best_address,)
        super().__init__(node_id, Commute, deps)
        self.display_name = "TfL API"
        self._has_car = has_car
        self._max_walk = max_walk
        self._best_address = best_address

    async def compute(
        self,
        location: Attempt[GeoPoint],
        poi: Attempt[PlaceOfInterest],
        no_bus: Attempt[Commute],
        with_bus: Attempt[Commute],
        best_address: Attempt[str] = None,
    ) -> Attempt[Commute]:

        if self._has_car and not no_bus.impossible:
            best_val = no_bus.value_or_none()
        elif with_bus.impossible and no_bus.impossible:
            errors = [e for e in (no_bus.error, with_bus.error) if e]
            return Attempt.impossible("; ".join(errors) if errors else "no transit route available")
        else:
            no_val = no_bus.value_or_none()
            with_val = with_bus.value_or_none()
            if no_val is None and with_val is None:
                return Attempt.impossible("no transit route available")
            empty = Commute(
                person=Person(name="", has_car=self._has_car),
                label="",
                destination=PlaceOfInterest(label="", address=""),
                duration=Quantity(0, "minute"),
                daily_cost=Money("0", "GBP"),
            )
            best_val = _CommuteRouter._pick_best_route(no_val or empty, with_val or empty)

        val = best_val
        if val is None:
            return Attempt.impossible("transit returned empty result")

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
        return Attempt.succeeded(result)
