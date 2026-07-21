from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode
from dag.node import Node
from dag.user_input_node import UserInputNode
from houses.commute import CostGroup, LegMode
from houses.geo import GeoPoint
from houses.model.domain import Commute


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


class CommuteSelectorNode(DerivedNode[Commute]):
    """Selects the best commute from walk, transit, drive, and bus options.

    Priority:
    1. Walking, if Google Routes returned a route within max_walk minutes
    2. Transit (vs bus — picks faster of the two)
    3. Driving (if available and not congestion zone)
    4. Bus (fallback when transit has a long first-leg walk)

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
        bus_result,
        rail_fare_result,
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
        self.bus_result = bus_result
        self.rail_fare_result = rail_fare_result
        self.is_child = is_child
        self._max_walk = max_walk
        deps = [origin, poi, transit_result, bus_result, rail_fare_result]
        if walk_result is not None:
            deps.append(walk_result)
        if drive_result is not None:
            deps.append(drive_result)
        super().__init__(node_id, Commute, tuple(deps))

    @property
    def _skip_impossible_dep_check(self) -> bool:
        """CommuteSelectorNode handles failed deps gracefully (e.g., fall back to bus)."""
        return True

    @staticmethod
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

    def _get_active_deps(self) -> tuple[Node, ...]:
        deps = [self.origin, self.poi, self.transit_result, self.bus_result, self.rail_fare_result]
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
        bus_result: Attempt[Commute],
        rail_fare_result: Attempt[Commute | None],
        walk: Attempt[Commute] | None = None,
        drive: Attempt[Commute] | None = None,
    ) -> Attempt[Commute]:
        if not origin.succeeded or not poi.succeeded:
            return self._impossible({"origin": origin, "poi": poi})

        candidates: list[Attempt[Commute]] = []

        # 1. Walk (Google Routes) — add if within max_walk
        if (
            walk is not None
            and walk.succeeded
            and walk.value_or_none() is not None
            and walk.value_or_none().duration.magnitude <= self._max_walk
        ):
            candidates.append(walk)

        # 2. Transit vs bus — pick the better of the two
        best_transit: Attempt[Commute] | None = None
        if transit.succeeded and transit.value_or_none() is not None:
            if bus_result.succeeded and bus_result.value_or_none() is not None:
                td = transit.value_or_none().duration.magnitude
                bd = bus_result.value_or_none().duration.magnitude
                best_transit = bus_result if bd < td - 5 else transit
            else:
                best_transit = transit
            candidates.append(best_transit)
        elif bus_result.succeeded and bus_result.value_or_none() is not None:
            # Transit failed — bus is a fallback
            candidates.append(bus_result)

        # 3. Drive
        if drive is not None and drive.succeeded and drive.value_or_none() is not None:
            candidates.append(drive)

        if not candidates:
            return self._impossible(
                {"walk_result": walk, "transit_result": transit, "drive_result": drive, "bus_result": bus_result}
            )

        # Pick fastest
        selected = min(candidates, key=lambda a: a.value_or_none().duration.magnitude)
        selected_src = None
        if selected is walk:
            selected_src = "walk"
        elif best_transit is not None and selected is best_transit:
            selected_src = "transit"
        elif selected is drive:
            selected_src = "drive"

        val = selected.value_or_none()
        if val is not None:
            val = replace(val, is_child=self.is_child)

        # Merge rail_fare cost when transit was selected
        if selected_src == "transit" and rail_fare_result.succeeded:
            rf_val = rail_fare_result.value_or_none()
            if rf_val and rf_val.daily_cost and rf_val.daily_cost.amount > 0 and val:
                new_details = self._replace_transit_group(val.details, rf_val.details)
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
                result["value"] = None
        if attempt.impossible:
            result["error"] = attempt.error
        result["provenance"] = (await self.build_provenance()).to_dict()
        return result

    async def to_json_value(self) -> dict:
        result = await super().to_json_value()
        result["is_child"] = self.is_child
        return result
