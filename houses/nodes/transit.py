from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.services_provider import get_services

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommuteLeg:
    """One segment of a commute, carrying TfL line / route name where available."""

    mode: str  # walk, bus, tube, train, dlr, overground, drive, cycle, park
    duration: Quantity
    line_name: str = ""  # e.g. "Bakerloo", "Great Western Railway"
    destination: str = ""  # e.g. "Oxford Circus", "Paddington"
    cost: float | None = None  # attributed cost (parking fees, etc.)
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
        cg_cost = float(cg.cost.amount) if cg.cost else None
        for i, leg in enumerate(cg.legs):
            mode_name = leg.mode.name.lower() if hasattr(leg.mode, "name") else str(leg.mode)
            legs.append(
                CommuteLeg(
                    mode=mode_name,
                    duration=Quantity(leg.duration_minutes, "minute"),
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

    def compute(self, transit: Attempt[dict]) -> Attempt[bool]:
        if not transit.succeeded:
            return Attempt.succeeded(False)
        val = transit.value_or_none() or {}
        walk_time = val.get("walk_time", 0)
        return Attempt.succeeded(walk_time > self._max_walk)


class TransitNode(DerivedNode[Commute]):
    """Computes a transit commute from best_location to a POI postcode.

    The value type is ``Commute`` (houses.model.domain), serialised via
    ``TypeAdapter`` through the base ``Node`` persistence layer.
    """

    def __init__(self, node_id: str, *, best_location, poi, has_car: bool, max_walk: int, best_address=None):
        deps: tuple[Node, ...] = (best_location, poi)
        if best_address is not None:
            deps = deps + (best_address,)
        super().__init__(node_id, Commute, deps)
        self.display_name = "TfL API"
        self._has_car = has_car
        self._max_walk = max_walk
        self._best_address = best_address

    async def compute(
        self, location: Attempt[GeoPoint], poi: Attempt[PlaceOfInterest], best_address: Attempt[str] = None
    ) -> Attempt[Commute]:
        if not location.succeeded:
            return self._impossible({"best_location": location})
        if not poi.succeeded:
            return self._impossible({"poi": poi})
        loc = location.value_or_none()
        poi_val = poi.value_or_none()
        dest_postcode = poi_val.postcode if isinstance(poi_val, PlaceOfInterest) else (poi_val or "")

        svc = get_services()
        commute = await svc.commute_router.route(
            loc,
            dest_postcode,
            has_car=self._has_car,
            max_walk_minutes=self._max_walk,
        )
        if commute.succeeded:
            val = commute.value_or_none()
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
                destination=PlaceOfInterest(label=label, postcode=val.destination.postcode),
                duration=val.duration,
                daily_cost=daily_cost,
                mode=mode,
                details=val.details,
            )
            return Attempt.succeeded(result)
        return self._impossible({"commute": commute})

    async def to_json(self) -> dict:
        attempt = await self.attempt()
        result: dict = {
            "status": attempt.status,
            "value": None,
        }
        result["succeeded"] = attempt.succeeded
        result["pending"] = attempt.pending
        result["impossible"] = attempt.impossible
        if attempt.succeeded:
            raw = attempt.value_or_none()
            result["value"] = self._adapter.dump_python(raw)
            legs = _build_details(raw)
            result["value"]["walk_time"] = sum(int(leg.duration.magnitude) for leg in legs if leg.mode == "walk")
            result["value"]["route_description"] = _route_description(legs)
            result["value"]["is_child"] = False
        if attempt.impossible:
            result["error"] = attempt.error
        result["provenance"] = (await self.build_provenance()).to_dict()
        return result
