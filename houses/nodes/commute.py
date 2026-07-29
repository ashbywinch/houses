from __future__ import annotations

import logging
from dataclasses import replace

from money import Money

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.commute import CostGroup, LegMode
from houses.geo import GeoPoint
from houses.model.domain import Commute

logger = logging.getLogger(__name__)


def _has_unpriced_transit(commute: Commute | None) -> bool:
    """Check if a commute has transit legs (train/tube) with cost=None.

    Used to decide whether to activate RailFareNode even when total
    daily_cost > 0 (e.g. park-and-ride added parking cost but the
    train fare is missing).
    """
    if commute is None:
        return False

    _transit_modes = {LegMode.TRAIN, LegMode.TUBE, LegMode.DLR, LegMode.OVERGROUND}
    return any(cg.cost is None and any(leg.mode in _transit_modes for leg in cg.legs) for cg in commute.details or ())


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


class CommuteSelectorNode(DerivedNode[Commute]):
    """Selects the best commute from walk, transit, and drive options.

    Does NOT apply NR fares — that is handled by MergeRailFareNode.

    Priority:
    1. Walking, if Google Routes returned a route within max_walk minutes
    2. Transit
    3. Driving (if available and not congestion zone)

    ``walk_result`` and ``drive_result`` are optional — omit them when
    walking/driving is not applicable (the selector treats them as
    impossible).
    """

    def __init__(
        self,
        node_id: str,
        *,
        origin,
        poi,
        transit_result,
        walk_result=None,
        drive_result=None,
        is_child: bool = False,
        max_walk: int = 30,
    ):
        self.origin = origin
        self.poi = poi
        self.walk_result = walk_result
        self.transit_result = transit_result
        self.drive_result = drive_result
        self.is_child = is_child
        self._max_walk = max_walk
        deps = [origin, poi, transit_result]
        if walk_result is not None:
            deps.append(walk_result)
        if drive_result is not None:
            deps.append(drive_result)
        super().__init__(node_id, Commute, tuple(deps))

    def _get_active_deps(self) -> tuple[Node, ...]:
        deps = [self.origin, self.poi, self.transit_result]
        if self.walk_result is not None:
            deps.append(self.walk_result)
        if self.drive_result is not None:
            deps.append(self.drive_result)
        return tuple(deps)

    def compute(
        self,
        origin: Attempt[GeoPoint],
        poi: Attempt[str],
        transit: Attempt[Commute],
        walk: Attempt[Commute] | None = None,
        drive: Attempt[Commute] | None = None,
    ) -> Attempt[Commute]:
        if not origin.succeeded or not poi.succeeded:
            return self._impossible({"origin": origin, "poi": poi})

        candidates = []

        # 1. Walk (Google Routes) — add if within max_walk
        if (
            walk is not None
            and walk.succeeded
            and walk.value_or_none() is not None
            and walk.value_or_none().duration.magnitude <= self._max_walk
        ):
            candidates.append(walk)

        # 2. Transit — add if succeeded
        best_transit: Attempt[Commute] | None = None
        if transit.succeeded and transit.value_or_none() is not None:
            best_transit = transit
            candidates.append(best_transit)

        # 3. Drive
        if drive is not None and drive.succeeded and drive.value_or_none() is not None:
            candidates.append(drive)

        if not candidates:
            return self._impossible({"walk_result": walk, "transit_result": transit, "drive_result": drive})

        # Pick fastest
        selected = min(candidates, key=lambda a: a.value_or_none().duration.magnitude)
        val = selected.value_or_none()
        if val is not None:
            val = replace(val, is_child=self.is_child)

        return Attempt.succeeded(val)

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
        if attempt.succeeded and attempt.value_or_none() is not None:
            try:
                result["value"] = self._adapter.dump_python(attempt.value_or_none(), mode="json")
            except Exception:
                logger.exception("Failed to serialize commute value to JSON")
                result["value"] = None
        if attempt.impossible:
            result["error"] = attempt.error
        result["provenance"] = (await self.build_provenance()).to_dict()
        return result

    async def to_json_value(self) -> dict:
        result = await super().to_json_value()
        result["is_child"] = self.is_child
        return result


class MergeRailFareNode(DerivedNode[Commute]):
    """Applies NR fare to the transit CostGroup when rail_fare provides a price.

    Takes the selected commute (from CommuteSelectorNode) and the rail_fare_if
    result. If the selected commute has transit legs and rail_fare has a priced
    transit group, replaces the transit cost with the NR fare.  Otherwise
    passes through unchanged.

    This is a separate node so provenance captures both the selected commute
    and the rail_fare independently, and staleness is tracked per-node.
    """

    def __init__(self, node_id: str, *, commute_result, rail_fare_result):
        self._commute_result = commute_result
        self._rail_fare_result = rail_fare_result
        super().__init__(node_id, Commute, (commute_result, rail_fare_result))

    def compute(
        self,
        commute: Attempt[Commute],
        rail_fare: Attempt[Commute | None],
    ) -> Attempt[Commute]:
        if not commute.succeeded:
            return commute
        val = commute.value_or_none()
        if val is None:
            return commute

        if not rail_fare.succeeded:
            return Attempt.succeeded(val)

        rf_val = rail_fare.value_or_none()
        if rf_val is None or rf_val.daily_cost is None or rf_val.daily_cost.amount <= 0:
            return Attempt.succeeded(val)

        # Check if the selected commute has transit legs
        _transit_modes = {LegMode.TRAIN, LegMode.TUBE, LegMode.DLR, LegMode.OVERGROUND}
        if not any(any(leg.mode in _transit_modes for leg in cg.legs) for cg in val.details):
            return Attempt.succeeded(val)

        # Apply the NR fare to the transit CostGroup
        new_details = _replace_transit_group(val.details, rf_val.details)
        total = Money("0", "GBP")
        for cg in new_details:
            if cg.cost is not None:
                total += cg.cost if isinstance(cg.cost, Money) else Money(str(cg.cost), "GBP")

        merged = replace(val, daily_cost=total, details=new_details)
        return Attempt.succeeded(merged)
