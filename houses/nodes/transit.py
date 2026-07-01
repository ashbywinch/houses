from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from money import Money
from pint import Quantity

from dag.attempt import Attempt, Provenance
from dag.computed_node import ComputedNode
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
    """Convert old Commute cost_groups/legs into new CommuteLeg tuples."""
    legs: list[CommuteLeg] = []
    for cg in commute.cost_groups:
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


class WalkLegCheckNode(ComputedNode[bool]):
    def __init__(self, node_id: str, *, transit_node, persons_source):
        super().__init__(node_id, bool, (transit_node, persons_source))

    def compute(self, transit: Attempt[dict],
                persons: Attempt[list]) -> Attempt[bool]:
        return Attempt.succeeded(False, Provenance("walk_check",
                                  description="simplified walk check"))


class TransitNode(ComputedNode[CommuteResult]):
    def __init__(self, node_id: str, *, best_location, poi, persons_source, best_address=None):
        deps = (best_location, poi, persons_source)
        if best_address is not None:
            deps = deps + (best_address,)
        super().__init__(node_id, object, deps)
        self._best_address = best_address

    async def compute(self, location: Attempt[GeoPoint],
                      poi: Attempt[str],
                      persons: Attempt[list],
                      best_address: Attempt[str] = None) -> Attempt[CommuteResult]:
        if not location.is_succeeded:
            return self._impossible({"best_location": location})
        if not poi.is_succeeded:
            return self._impossible({"poi": poi})
        if not persons.is_succeeded:
            return self._impossible({"persons_source": persons})
        loc: GeoPoint = location.value_or_none()
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

        commute = await get_commute(
            loc,
            dest_postcode,
            has_car=has_car,
            max_walk_minutes=max_walk,
        )
        if commute.is_succeeded:
            val = commute.value_or_none()
            details = _build_details(val)
            # Use the POI label from settings (user-defined), not the raw API string
            parts = self._id.split("/")
            label = parts[2] if len(parts) >= 3 else (val.destination_label or "")
            raw_mode = val.mode if hasattr(val, 'mode') else "transit"
            mode = raw_mode.name.lower() if isinstance(raw_mode, Enum) else str(raw_mode)
            # Detect walking: if all legs are "walk" mode
            if details and all(leg.mode == "walk" for leg in details):
                mode = "walk"
            return Attempt.succeeded(
                CommuteResult(
                    duration=Quantity(val.duration_minutes or 0, "minute"),
                    daily_cost=val.daily_cost_gbp,
                    label=label,
                    mode=mode,
                    details=details,
                    route_description=_route_description(details),
                    is_child=is_child,
                ),
                Provenance("TfL API",
                           description=f"transit {loc.lat},{loc.lon} → {dest_postcode}"),
            )
        err = commute._reason or commute._error or "unknown"
        return Attempt.impossible(f"get_commute: {err}",
                                   Provenance("TfL API",
                                              description=f"transit {loc.lat},{loc.lon} → {dest_postcode}"))

    async def to_json(self) -> dict:
        attempt = await self.attempt()
        if attempt.is_succeeded:
            cr = attempt.value_or_none()
            return {
                "succeeded": True,
                "value": {
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
                },
                "error": None,
                "provenance": self._provenance_to_json(attempt.provenance),
            }
        return {
            "succeeded": False, "value": None,
            "error": attempt._error,
            "provenance": self._provenance_to_json(attempt.provenance),
        }

    def _load_from_db(self) -> None:
        from dag.persistence import latest_node_result
        stored = latest_node_result(self._id)
        if stored is not None:
            succeeded = stored["succeeded"]
            if succeeded:
                v = stored["value"]
                poi_label = ""
                parts = self._id.split("/")
                if len(parts) >= 3:
                    poi_label = parts[2]
                cr = CommuteResult(
                    duration=Quantity(v.get("duration", {}).get("value", 0), "minute"),
                    daily_cost=(
                        Money(v["daily_cost"]["amount"], v["daily_cost"]["currency"])
                        if v.get("daily_cost") and v["daily_cost"].get("amount") else None
                    ),
                    label=poi_label or v.get("label", ""),
                    mode=v.get("mode", "transit"),
                    route_description=v.get("route_description", ""),
                )
                prov = Provenance(stored.get("provenance", {}).get("label", ""))
                self._cached = Attempt.succeeded(cr, prov)
            else:
                self._cached = Attempt.impossible(stored.get("error", "unknown"))
            self._db_created_at = stored.get("_persisted_at", "")
            dep_ts = stored.get("dep_timestamps")
            self._loaded_dep_timestamps = dep_ts if isinstance(dep_ts, dict) else {}
            self._computed_at = __import__("time").monotonic()
            self._persisted_at = __import__("time").monotonic()
