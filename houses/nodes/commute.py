from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import override

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode
from dag.expression import Choose, Expression, Ref
from dag.node import Node
from houses.commute import CostGroup, LegMode
from houses.geopoint import GeoPoint
from houses.model.domain import Commute

logger = logging.getLogger(__name__)
MINUTES_PER_HOUR = 60
GOOD_COMMUTE_MIN = 30
BRACKNELL_WARN_COMMUTE_MIN = 60
STANDARD_GOOD_COMMUTE_MIN = 45
STANDARD_WARN_COMMUTE_MIN = 75

def transit_legs(commute: Commute | None) -> bool:
    """True when the commute contains train/tube/DLR/Overground legs.

    Callers must check ``infeasible`` first — the ``details`` accessor
    raises on an infeasible commute.
    """
    if commute is None:
        return False
    _transit_modes = {LegMode.TRAIN, LegMode.TUBE, LegMode.DLR, LegMode.OVERGROUND}
    return any(any(leg.mode in _transit_modes for leg in cg.legs) for cg in commute.details)


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


def needs_rail_fare(transit: Attempt, selected: Attempt | None = None) -> bool:
    """True when the SELECTED commute has unpriced transit legs needing an NR fare.

    ``transit`` is the transit alternative; ``selected`` is the commute the
    selector actually chose.  When the selection is drive/walk (or still
    pending), the fare is irrelevant — the fare node must never be
    calculated for a route that wasn't chosen.
    """
    if not transit.succeeded:
        return False
    val = transit.value_or_none()
    if val is None or val.infeasible:
        # No route — nothing to price; never touch .details on an
        # infeasible commute (the accessor raises).
        return False
    if selected is not None:
        if not selected.succeeded:
            return False
        sel = selected.value_or_none()
        if sel is None or sel.infeasible or not transit_legs(sel):
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
    if minutes < MINUTES_PER_HOUR:
        return f"{minutes}m"
    h = minutes // MINUTES_PER_HOUR
    r = minutes % MINUTES_PER_HOUR
    return f"{h}h{r}" if r else f"{h}h"


def commute_colour(minutes: int | None, bracknell: bool = False) -> str:
    """Match the old ``houses.web.card_data.commute_colour`` thresholds."""
    if minutes is None:
        return "muted"
    if bracknell:
        return "good" if minutes < GOOD_COMMUTE_MIN else "warn" if minutes <= BRACKNELL_WARN_COMMUTE_MIN else "bad"
    return "good" if minutes < STANDARD_GOOD_COMMUTE_MIN else "warn" if minutes <= STANDARD_WARN_COMMUTE_MIN else "bad"


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


@dataclass(frozen=True)
class CommuteSelectorOptions:
    """Wiring for ``CommuteSelectorNode``: the alternative commute inputs.

    ``walk_result``/``drive_result`` are optional — omit them when
    walking/driving is not applicable (the selector treats them as
    impossible).  ``acceptable_modes`` is empty for "every mode accepted".
    """

    origin: Node
    poi: Node
    transit_result: Node
    walk_result: Node | None = None
    drive_result: Node | None = None
    is_child: bool = False
    max_walk_node: Node | None = None
    acceptable_modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommuteSelectorInputs:
    """Compute inputs for ``CommuteSelectorNode``, bound by dep name."""

    origin: Attempt[GeoPoint]
    poi: Attempt[str]
    max_walk: Attempt[int] | None = None
    transit: Attempt[Commute] | None = None
    walk: Attempt[Commute] | None = None
    drive: Attempt[Commute] | None = None


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

    A transit "no route" result is modelled as a *succeeded* commute with
    ``infeasible=True`` — never as an impossible attempt — so the
    framework runs the selector and ``_pick_best`` can fall back to
    drive/walk.  A genuinely failed transit API call is impossible and
    propagates as usual.
    """

    # The expression is always the Choose built in __init__ (the base
    # class attribute is `Expression | None` — narrow it so pyrefly sees
    # evaluate()/last_results).
    _expression: Choose

    def __init__(
        self,
        node_id: str,
        *,
        options: CommuteSelectorOptions,
    ):
        self.origin = options.origin
        self.poi = options.poi
        self.walk_result = options.walk_result
        self.transit_result = options.transit_result
        self.drive_result = options.drive_result
        self.is_child = options.is_child
        self._max_walk = 30
        self._max_walk_node = options.max_walk_node
        # Empty (unset/legacy) means every mode is acceptable — the old
        # behaviour.  An explicit set EXCLUDES the modes the person won't
        # accept: a train-only POI is never scored by a car route.
        self._acceptable_modes = tuple(options.acceptable_modes)
        # deps mirror the alternatives: an excluded mode is not a
        # dependency — a permanently pending excluded node must not stall
        # the selector's refresh (same freeze the bootstrap fix addresses).
        # max_walk sits BEFORE the conditional alternatives so positional
        # compute matching stays stable whether walk/drive are present.
        deps = [options.origin, options.poi]
        names = ["origin", "poi"]
        if options.max_walk_node is not None:
            deps.append(options.max_walk_node)
            names.append("max_walk")
        if self._mode_acceptable("transit"):
            deps.append(options.transit_result)
            names.append("transit")
        if options.walk_result is not None and self._mode_acceptable("walk"):
            deps.append(options.walk_result)
            names.append("walk")
        if options.drive_result is not None and self._mode_acceptable("car"):
            deps.append(options.drive_result)
            names.append("drive")
        super().__init__(node_id, Commute, tuple(deps), dep_names=tuple(names))

        # Build expression once — cached so last_results persists across calls
        alts: dict[str, Expression] = {}
        if self.walk_result is not None and self._mode_acceptable("walk"):
            alts["walk"] = Ref(self.walk_result)
        if self._mode_acceptable("transit"):
            alts["transit"] = Ref(self.transit_result)
        if self.drive_result is not None and self._mode_acceptable("car"):
            alts["drive"] = Ref(self.drive_result)
        self._expression = Choose(
            alternatives=alts,
            selector=self._pick_best,
            description="Selects the fastest feasible commute mode",
        )

    def _mode_acceptable(self, mode: str) -> bool:
        """Whether ``mode`` may be selected.  Unset = all acceptable."""
        return not self._acceptable_modes or mode in self._acceptable_modes

    @override
    def _get_active_deps(self) -> tuple[Node, ...]:
        deps = [self.origin, self.poi]
        if self._max_walk_node is not None:
            deps.append(self._max_walk_node)
        if self._mode_acceptable("transit"):
            deps.append(self.transit_result)
        if self.walk_result is not None and self._mode_acceptable("walk"):
            deps.append(self.walk_result)
        if self.drive_result is not None and self._mode_acceptable("car"):
            deps.append(self.drive_result)
        return tuple(deps)

    @property
    @override
    def expression(self):
        return self._expression

    def _pick_best(self, results):
        """Choose the fastest feasible commute from available results.

        A walk that exceeds ``max_walk`` is a last resort: it loses to any
        feasible alternative, but is still returned when nothing else
        works — an over-threshold walk beats 'no route'.
        """
        best = None
        best_duration = None
        fallback_walk: tuple[float, str] | None = None
        for name, attempt in results.items():
            if not attempt.succeeded:
                continue
            val = attempt.value_or_none()
            if val is None or val.infeasible:
                continue
            # Walk has a max distance limit — prefer alternatives, but
            # keep the walk as a fallback when none exist.
            if name == "walk" and val.duration.magnitude > self._max_walk:
                if fallback_walk is None or val.duration.magnitude < fallback_walk[0]:
                    fallback_walk = (val.duration.magnitude, name)
                continue
            d = val.duration.magnitude
            if best is None or d < best_duration:
                best = name
                best_duration = d
        if best is None and fallback_walk is not None:
            return fallback_walk[1]
        return best

    @override
    def compute(self, **kwargs) -> Attempt[Commute]:
        inputs = CommuteSelectorInputs(**kwargs)
        mw_val = inputs.max_walk.value_or_none() if inputs.max_walk is not None else None
        if mw_val is not None:
            self._max_walk = int(mw_val)
        result = self.expression.evaluate()
        if result.succeeded and result.value is not None:
            val = replace(result.value, is_child=self.is_child)
            return Attempt.succeeded(val)
        # Build detailed error from all alternatives
        errors = []
        for name in ("walk", "transit", "drive"):
            r = self.expression.last_results.get(name) if self.expression.last_results else None
            if r and r.impossible:
                errors.append(f"{name}: {r.error}")
        if errors:
            return Attempt.impossible("; ".join(errors))
        return result

    @override
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def to_json(self) -> dict:
        attempt = await self.attempt()
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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
                value = self._adapter.dump_python(attempt.value_or_none(), mode="json")
                # Rename private _details field back to details for the frontend
                if isinstance(value, dict) and "_details" in value:
                    value["details"] = value.pop("_details")
                result["value"] = value
            # lucidlint: ignore broad-except deliberate broad catch — boundary/fallback per coding-standards.md
            except Exception:
                logger.exception("Failed to serialize commute value to JSON")
                result["value"] = None
        if attempt.impossible:
            info = attempt.error_info
            result["error"] = (info.display_message if info is not None else attempt.error) or attempt.error
        result["provenance"] = (await self.build_provenance()).to_dict()
        return result

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    @override
    async def to_json_value(self) -> dict:
        result = await super().to_json_value()
        result["is_child"] = self.is_child
        if "_details" in result:
            result["details"] = result.pop("_details")
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
        super().__init__(
            node_id,
            Commute,
            (commute_result, rail_fare_result),
            dep_names=("commute", "rail_fare"),
        )

    @property
    @override
    def provenance_formula(self):

        val = self._attempt.value_or_none()
        if not self._attempt.succeeded or val is None:
            return None
        lines: list[FormulaLine] = []
        commute_att = self._commute_result.latest_attempt()
        if commute_att.succeeded:
            cv = commute_att.value_or_none()
            if cv is not None:
                lines.append(FormulaLine(label="Commute", value=str(cv.daily_cost)))
        fare_att = self._rail_fare_result.latest_attempt()
        if fare_att.succeeded:
            rf = fare_att.value_or_none()
            if rf is not None and rf.daily_cost is not None and rf.daily_cost.amount > 0 and transit_legs(val):
                lines.append(FormulaLine(label="Rail fare", value=str(rf.daily_cost)))
        return Formula(lines=lines, result=str(val.daily_cost))

    @override
    def _get_active_deps(self) -> tuple[Node, ...]:
        """The rail-fare input is a CONDITIONAL dependency: it is only
        activated when the selected commute uses transit legs.  A
        drive/walk selection leaves the fare node untouched — it stays
        pending (never calculated) and its status can't affect the merge.
        """
        deps: list[Node] = [self._commute_result]
        sel = self._commute_result.latest_attempt()
        if sel.succeeded:
            val = sel.value_or_none()
            if val is not None and not val.infeasible and transit_legs(val):
                deps.append(self._rail_fare_result)
        return tuple(deps)

    @override
    @staticmethod
    def compute(
        commute: Attempt[Commute],
        rail_fare: Attempt[Commute | None] | None = None,
    ) -> Attempt[Commute]:
        if not commute.succeeded:
            return commute
        val = commute.value_or_none()
        if val is None:
            return commute

        # rail_fare is None when the fare dependency was not activated
        # (drive/walk selection) — nothing to apply.
        if rail_fare is None or not rail_fare.succeeded:
            return Attempt.succeeded(val)

        rf_val = rail_fare.value_or_none()
        if rf_val is None or rf_val.daily_cost is None or rf_val.daily_cost.amount <= 0:
            return Attempt.succeeded(val)

        # Check if the selected commute has transit legs
        if not transit_legs(val):
            return Attempt.succeeded(val)

        # Apply the NR fare to the transit CostGroup
        new_details = _replace_transit_group(val.details, rf_val.details)
        total = Money(amount="0", currency="GBP")
        for cg in new_details:
            if cg.cost is not None:
                if not isinstance(cg.cost, Money):
                    raise TypeError(f"CostGroup.cost must be Money or None, got {type(cg.cost).__name__}: {cg.cost}")
                total += cg.cost

        merged = replace(val, daily_cost=total, _details=new_details)
        return Attempt.succeeded(merged)
