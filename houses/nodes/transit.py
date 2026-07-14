from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from houses.context import get_services
from money import Money
from pint import Quantity

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.geo import GeoPoint
from houses.model.domain import Commute
from houses.routing import get_commute

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommuteLeg:
    """One segment of a commute, carrying TfL line / route name where available."""

    mode: str  # walk, bus, tube, train, dlr, overground, drive, cycle
    duration: Quantity
    line_name: str = ""  # e.g. "Bakerloo", "Great Western Railway"
    destination: str = ""  # e.g. "Oxford Circus", "Paddington"


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
    "walk": "Walk", "bus": "Bus", "tube": "Tube", "train": "Train",
    "dlr": "DLR", "overground": "Overground", "tram": "Tram",
    "drive": "Drive", "cycle": "Cycle", "park": "Park",
}


def _build_details(commute: Commute) -> tuple[CommuteLeg, ...]:
    """Convert a Commute's cost groups into CommuteLeg tuples."""
    legs: list[CommuteLeg] = []
    for cg in commute.details:
        for leg in cg.legs:
            mode_name = leg.mode.name.lower() if hasattr(leg.mode, 'name') else str(leg.mode)
            legs.append(CommuteLeg(
                mode=mode_name,
                duration=Quantity(leg.duration_minutes, "minute"),
                line_name=leg.line_name,
                destination=leg.end_station,
            ))
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


def _serialize_commute_result(cr: CommuteResult) -> dict:
    return {
        "duration": {"value": int(cr.duration.magnitude), "unit": "minute"},
        "daily_cost": (
            {"amount": float(cr.daily_cost.amount), "currency": str(cr.daily_cost.currency)}
            if cr.daily_cost else {"amount": 0, "currency": "GBP"}
        ),
        "label": cr.label,
        "mode": cr.mode,
        "route_description": cr.route_description,
        "is_child": cr.is_child,
        "source_url": cr.source_url,
        "destination_url": cr.destination_url,
    }


def _deserialize_commute_result(data: dict) -> CommuteResult:
    dur = data.get("duration", {})
    dc = data.get("daily_cost") or {}
    return CommuteResult(
        duration=Quantity(dur.get("value", 0), "minute"),
        daily_cost=(
            Money(dc["amount"], dc["currency"])
            if dc.get("amount") else None
        ),
        label=data.get("label", ""),
        mode=data.get("mode", "transit"),
        route_description=data.get("route_description", ""),
        is_child=data.get("is_child", False),
        source_url=data.get("source_url", ""),
        destination_url=data.get("destination_url", ""),
    )


class WalkLegCheckNode(DerivedNode[bool]):
    def __init__(self, node_id: str, *, transit_node, persons_source):
        super().__init__(node_id, bool, (transit_node, persons_source))

    def compute(self, transit: Attempt[dict],
                persons: Attempt[list]) -> Attempt[bool]:
        return Attempt.succeeded(False)


class TransitNode(DerivedNode[dict]):
    """Computes a transit commute from best_location to a POI postcode.

    Persists and loads a serialised dict (not CommuteResult directly)
    so the value survives restarts without needing to reconstruct
    Money/Quantity types on every load.  The dict is lazily deserialised
    back to CommuteResult for consumption in ``to_json()``.
    """

    def __init__(self, node_id: str, *, best_location, poi, persons_source, best_address=None):
        deps: tuple[Node, ...] = (best_location, poi, persons_source)
        if best_address is not None:
            deps = deps + (best_address,)
        super().__init__(node_id, dict, deps)
        self._best_address = best_address
        # Upgrade a dict cached value to CommuteResult if loaded from DB
        self._commute_cache: CommuteResult | None = None
        if self._cached is not None and self._cached.succeeded:
            val = self._cached.value
            if isinstance(val, dict):
                self._commute_cache = _deserialize_commute_result(val)

    async def compute(self, location: Attempt[GeoPoint],
                      poi: Attempt[str],
                      persons: Attempt[list],
                      best_address: Attempt[str] = None) -> Attempt[dict]:
        if not location.succeeded:
            return self._impossible({"best_location": location})
        if not poi.succeeded:
            return self._impossible({"poi": poi})
        if not persons.succeeded:
            return self._impossible({"persons_source": persons})
        loc = location.value_or_none()
        dest_postcode = poi.value_or_none()

        # Extract person name from node_id: {rid}/{person}/{poi_label}/computed_transit
        name = self._id.split("/")[1]
        persons_list = persons.value_or_none() or []
        has_car = False
        is_child = False
        max_walk = 30
        for p in persons_list:
            pn = p["name"] if isinstance(p, dict) else getattr(p, "name", "")
            if pn == name:
                hc = p.get("has_car", False) if isinstance(p, dict) else getattr(p, "has_car", False)
                has_car = bool(hc)
                ic = p.get("is_child", False) if isinstance(p, dict) else getattr(p, "is_child", False)
                is_child = bool(ic)
                max_walk = int(p.get("bus_walk_penalty_minutes", 30)) if isinstance(p, dict) else 30
                break

        svc = get_services()
        commute = await svc.commute_router.route(
            loc,
            dest_postcode,
            has_car=has_car,
            max_walk_minutes=max_walk,
        )
        if commute.succeeded:
            val = commute.value_or_none()
            details = _build_details(val)
            parts = self._id.split("/")
            label = parts[2] if len(parts) >= 3 else (val.destination.label or "")
            raw_mode = val.mode if hasattr(val, 'mode') else "transit"
            mode = raw_mode.name.lower() if isinstance(raw_mode, Enum) else str(raw_mode)
            if details and all(leg.mode == "walk" for leg in details):
                mode = "walk"
            cr = CommuteResult(
                duration=Quantity(int(val.duration.magnitude), "minute") if val.duration else Quantity(0, "minute"),
                daily_cost=val.daily_cost,
                label=label,
                mode=mode,
                details=details,
                route_description=_route_description(details),
                is_child=is_child,
            )
            self._commute_cache = cr
            return Attempt.succeeded(_serialize_commute_result(cr))

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
            cr = self._commute_cache
            if cr is None and isinstance(attempt.value, dict):
                cr = _deserialize_commute_result(attempt.value)
            if cr is not None:
                result["value"] = _serialize_commute_result(cr)
            else:
                result["value"] = attempt.value
        if attempt.impossible:
            result["error"] = attempt.error
        result["provenance"] = (await self.build_provenance()).to_dict()
        return result

    async def build_provenance(self):
        description = ""
        if self._commute_cache:
            description = f"transit to {self._commute_cache.label}"
        return Provenance(label="TfL API", description=description)
