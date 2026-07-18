from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from money import Money

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode
from dag.user_input_node import UserInputNode
from houses.commute import CostGroup, LegMode
from houses.geo import GeoPoint
from houses.model.domain import Commute
from houses.stations import Station


def _has_unpriced_transit(commute: Commute | None) -> bool:
    """Check if a commute has transit legs (train/tube) with cost=None.

    Used to decide whether to activate RailFareNode even when total
    daily_cost > 0 (e.g. park-and-ride added parking cost but the
    train fare is missing).
    """
    if commute is None:
        return False

    _transit_modes = {LegMode.TRAIN, LegMode.TUBE, LegMode.DLR, LegMode.OVERGROUND}
    return any(
        cg.cost is None and any(leg.mode in _transit_modes for leg in cg.legs)
        for cg in commute.details or ()
    )


def _bus_condition(walk_check: Attempt) -> bool:
    """True when walk check succeeded and walk is too long (needs bus)."""
    return walk_check.succeeded and bool(walk_check.value)


def _needs_rail_fare(transit: Attempt) -> bool:
    """True when transit has no cost assigned yet and needs an NR fare."""
    if not transit.succeeded:
        return False
    val = transit.value_or_none()
    if val is None:
        return False
    if val.daily_cost is None or val.daily_cost.amount == 0:
        return True
    return _has_unpriced_transit(val)


def _replace_transit_group(
    selected_details: tuple[CostGroup, ...],
    rail_fare_details: tuple[CostGroup, ...],
) -> tuple[CostGroup, ...]:
    """Replace the transit CostGroup in selected_details with the one from
    rail_fare_details (which has the correct NR fare cost).

    The transit CostGroup is the one containing train/tube legs. If no such
    group is found in selected, append the rail_fare group instead.
    """

    _transit_modes = {LegMode.TRAIN, LegMode.TUBE, LegMode.DLR, LegMode.OVERGROUND}

    # Find the transit CostGroup from rail_fare (the one with the NR price)
    replacement = None
    for cg in rail_fare_details:
        if any(leg.mode in _transit_modes for leg in cg.legs):
            replacement = cg
            break
    if replacement is None and rail_fare_details:
        # Fallback: use the first rail_fare group if no transit mode found
        replacement = rail_fare_details[0]
    if replacement is None:
        return selected_details

    # Replace matching group in selected, or append if not found
    result = list(selected_details)
    for i, cg in enumerate(result):
        if any(leg.mode in _transit_modes for leg in cg.legs):
            result[i] = replacement
            break
    else:
        result.append(replacement)
    return tuple(result)


def format_duration(minutes: int | None) -> str:
    """Match the old ``houses.web.card_data._dur`` format.

    Returns ``""`` for ``None``, ``"9m"`` for <60, ``"1h2"`` (no space),
    ``"2h"`` for exact hours.
    """
    if minutes is None:
        return ""
    if minutes < 60:
        return f"{minutes}m"
    h = minutes // 60
    r = minutes % 60
    return f"{h}h{r}" if r else f"{h}h"


def commute_colour(minutes: int | None, bracknell: bool = False) -> str:
    """Match the old ``houses.web.card_data.commute_colour`` thresholds."""
    if minutes is None:
        return "muted"
    if bracknell:
        return "good" if minutes < 30 else "warn" if minutes <= 60 else "bad"
    return "good" if minutes < 45 else "warn" if minutes <= 75 else "bad"


def _serialize_value(val: Any) -> Any:
    if isinstance(val, Money):
        return {"amount": float(val.amount), "currency": val.currency}
    if hasattr(val, "units") and hasattr(val, "magnitude"):
        return {"value": float(val.magnitude), "unit": str(val.units)}
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, timedelta):
        return val.total_seconds()
    if isinstance(val, Enum):
        return val.name.lower()
    if is_dataclass(val) and not isinstance(val, type):
        cls_name = type(val).__name__
        if cls_name == "CommuteResult":
            dc = val.daily_cost
            return {
                "duration": {"value": int(val.duration.magnitude), "unit": "minute"},
                "daily_cost": (
                    {"amount": float(dc.amount), "currency": str(dc.currency)}
                    if dc
                    else {"amount": 0, "currency": "GBP"}
                ),
                "label": val.label,
                "mode": val.mode,
                "route_description": val.route_description,
                "is_child": val.is_child,
                "details": [_serialize_leg(leg) for leg in val.details],
            }
        result: dict[str, Any] = {}
        for f in fields(val):
            result[f.name] = _serialize_value(getattr(val, f.name))
        return result
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    return val


def _serialize_leg(leg) -> dict:
    mode = leg.mode.name.lower() if isinstance(leg.mode, Enum) else leg.mode
    return {
        "mode": mode,
        "duration": {"value": int(leg.duration.magnitude), "unit": "minute"},
        "line_name": leg.line_name,
        "destination": leg.destination,
    }


def commute_input_node(node_id: str) -> UserInputNode[Commute]:
    return _CommuteInputNode(node_id)


class _CommuteInputNode(UserInputNode[dict]):
    """A UserInputNode that holds a raw Commute (not serialised to dict).

    Values are not persisted to the DB — they are ephemeral results
    pushed from the commute pipeline during enrichment.
    """

    def __init__(self, node_id: str) -> None:
        super().__init__(node_id, dict)

    def push(self, value: Commute, source_label: str = "") -> None:
        self._value = value
        self._source_label = source_label
        self._persisted_at = datetime.now(UTC)
        self._db_created_at = datetime.now(UTC).isoformat()
        self.changed.emit()

    async def attempt(self) -> Attempt[Commute]:
        if self._value is not None:
            return Attempt.succeeded(self._value)
        return Attempt.pending()

    async def build_provenance(self):
        return Provenance.from_label(self._source_label)


class RailFareNode(DerivedNode[Commute]):
    """Computes National Rail fare for a commute when TfL has no price.

    Extracts the destination London terminal from the TfL route legs
    (the last heavy-rail leg), then looks up the fare from the property's
    nearest station to that terminal.  This avoids geocoding POI postcodes
    to find stations — the NR fare system uses terminal zones (PAD, VIC,
    WAT, …) as destinations, and the route already tells us which one.
    """

    def __init__(self, node_id: str, *, transit_result, best_location):
        self.transit_result = transit_result
        self.best_location = best_location
        super().__init__(node_id, Commute, (transit_result,))

    def _get_active_deps(self):
        deps = [self.transit_result]
        transit_attempt = self.transit_result.latest_attempt()
        if transit_attempt.succeeded:
            val = transit_attempt.value_or_none()
            if val is None:
                deps.append(self.best_location)
            elif isinstance(val, dict):
                dc = val.get("daily_cost") or {}
                if float(dc.get("amount", 0)) == 0:
                    deps.append(self.best_location)
            else:
                if float(val.daily_cost.amount) == 0:
                    deps.append(self.best_location)
        return tuple(deps)

    async def compute(
        self, transit_attempt: Attempt[dict | Commute], location: Attempt[GeoPoint] = None
    ) -> Attempt[Commute]:
        if not transit_attempt.succeeded:
            return Attempt.impossible("transit not succeeded")
        val = transit_attempt.value_or_none()
        if val is None:
            return Attempt.impossible("transit value is None")

        # Normalise to Commute (TransitNode currently returns dict)
        if isinstance(val, dict):
            dc = val.get("daily_cost") or {}
            if float(dc.get("amount", 0)) > 0:
                return transit_attempt  # type: ignore[return-value]
            if not location or not location.succeeded:
                return Attempt.impossible("best_location not available")

            from houses.rail_fare_registry import get_rail_fare_registry
            from houses.transit_route import FALLBACK_TUBE_SINGLE_GBP, get_tube_leg_fare

            registry = get_rail_fare_registry()

            origin = registry.nearest_station(location.value_or_none())
            if not origin:
                return Attempt.impossible("origin station not found near property")

            details = val.get("details") or []
            terminal_station: Station | None = None
            for leg in reversed(details):
                mode = leg.get("mode", "")
                dest_name = leg.get("destination", "")
                if mode in ("train", "tube", "dlr", "overground") and dest_name:
                    stn = registry.find_station(dest_name)
                    if stn:
                        terminal_station = stn
                        break

            if terminal_station is None:
                return Attempt.impossible("terminal station not found in route legs")

            fare = registry.fare_between(origin, terminal_station)
            if fare is None:
                dummy_lon = Station("London Terminals", "LON", GeoPoint(0, 0))
                fare = registry.fare_between(origin, dummy_lon)
            if fare is None:
                return Attempt.impossible(f"no fare {origin.crs}→{terminal_station.crs}")
            tube_fare = await get_tube_leg_fare(terminal_station, "") or Money(FALLBACK_TUBE_SINGLE_GBP, "GBP")
            total = (fare + tube_fare) * 2
            val["daily_cost"] = {"amount": float(total.amount), "currency": "GBP"}
            # Can't return a Commute without reconstructing one from dict,
            # so return the Attempt at type:ignore (callers do not assert type)
            return Attempt.succeeded(val)  # type: ignore[return-value]

        # Commute path (once TransitNode is refactored)
        commute = val
        if float(commute.daily_cost.amount) > 0:
            return transit_attempt
        if not location or not location.succeeded:
            return Attempt.impossible("best_location not available")

        from houses.rail_fare_registry import get_rail_fare_registry
        from houses.transit_route import FALLBACK_TUBE_SINGLE_GBP, get_tube_leg_fare

        registry = get_rail_fare_registry()

        origin = registry.nearest_station(location.value_or_none())
        if not origin:
            return Attempt.impossible("origin station not found near property")

        details = commute.details  # tuple[CostGroup, ...]
        terminal_station: Station | None = None
        for cg in reversed(details):
            for leg in reversed(cg.legs):
                mode_name = leg.mode.name.lower()
                if mode_name in ("train", "tube", "dlr", "overground") and leg.end_station:
                    stn = registry.find_station(leg.end_station)
                    if stn:
                        terminal_station = stn
                        break
            if terminal_station:
                break

        if terminal_station is None:
            return Attempt.impossible("terminal station not found in route legs")

        fare = registry.fare_between(origin, terminal_station)
        if fare is None:
            dummy_lon = Station("London Terminals", "LON", GeoPoint(0, 0))
            fare = registry.fare_between(origin, dummy_lon)
        if fare is None:
            return Attempt.impossible(f"no fare {origin.crs}→{terminal_station.crs}")
        tube_fare = await get_tube_leg_fare(terminal_station, "") or Money(FALLBACK_TUBE_SINGLE_GBP, "GBP")
        total = (fare + tube_fare) * 2

        # Attribute the fare to transit CostGroup(s) so frontend displays it

        _transit_modes = {LegMode.TRAIN, LegMode.TUBE, LegMode.DLR, LegMode.OVERGROUND}
        new_details = list(commute.details)
        for i, cg in enumerate(new_details):
            if cg.operator == "TfL" or any(leg.mode in _transit_modes for leg in cg.legs):
                new_details[i] = replace(cg, cost=total)
        new_details_t = tuple(new_details)

        new_commute = replace(commute, daily_cost=Money(str(total.amount), "GBP"), details=new_details_t)
        return Attempt.succeeded(new_commute)


class CommuteSelectorNode(DerivedNode[Commute]):
    def __init__(
        self,
        node_id: str,
        *,
        origin,
        poi,
        transit_result,
        bus_result,
        rail_fare_result,
        is_child: bool = False,
    ):
        self.origin = origin
        self.poi = poi
        self.transit_result = transit_result
        self.bus_result = bus_result
        self.rail_fare_result = rail_fare_result
        self.is_child = is_child
        super().__init__(node_id, Commute, (origin, poi, transit_result, bus_result, rail_fare_result))

    def compute(
        self,
        origin: Attempt[GeoPoint],
        poi: Attempt[str],
        transit: Attempt[Commute],
        bus_result: Attempt[Commute],
        rail_fare_result: Attempt[Commute | None],
    ) -> Attempt[Commute]:
        if not origin.succeeded or not poi.succeeded:
            return self._impossible({"origin": origin, "poi": poi})

        selected = None
        if transit.succeeded:
            if bus_result.succeeded and bus_result.value_or_none() is not None:
                transit_dur = transit.value_or_none().duration.magnitude
                bus_dur = bus_result.value_or_none().duration.magnitude
                selected = bus_result if bus_dur < transit_dur - 5 else transit
            else:
                selected = transit
        elif bus_result.succeeded and bus_result.value_or_none() is not None:
            selected = bus_result

        if selected is not None:
            val = selected.value_or_none()
            # Only merge rail_fare cost when transit was selected (not bus)
            if selected is transit and rail_fare_result.succeeded:
                rf_val = rail_fare_result.value_or_none()
                if rf_val and rf_val.daily_cost and rf_val.daily_cost.amount > 0 and val:
                    new_details = _replace_transit_group(val.details, rf_val.details)
                    from money import Money

                    total = Money("0", "GBP")
                    for cg in new_details:
                        if cg.cost is not None:
                            total += cg.cost if isinstance(cg.cost, Money) else Money(str(cg.cost), "GBP")
                    merged = replace(
                        val,
                        daily_cost=total,
                        details=new_details,
                    )
                    return Attempt.succeeded(merged)
            return selected

        return self._impossible({"transit_result": transit, "bus_result": bus_result})

    async def to_json(self) -> dict:
        attempt = await self.attempt()
        result: dict = {
            "status": attempt.status,
            "value": None,
            "is_child": self.is_child,
        }
        result["succeeded"] = attempt.succeeded
        result["pending"] = attempt.pending
        result["impossible"] = attempt.impossible
        if attempt.succeeded:
            result["value"] = self._adapter.dump_python(attempt.value_or_none(), mode="json")
        if attempt.impossible:
            result["error"] = attempt.error
        result["provenance"] = (await self.build_provenance()).to_dict()
        return result

    @property
    def _skip_impossible_dep_check(self) -> bool:
        """Transit/bus/rail dep failures are handled gracefully in compute()."""
        return True
