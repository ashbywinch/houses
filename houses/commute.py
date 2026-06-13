"""Commute value objects — journey legs, cost groups, and commute results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from money import Money

from houses.stations import Station


def _render_leg_description(leg: JourneyLeg) -> str:
    """Build a human-readable leg description from raw fields.

    Uses the same format regardless of which API generated the leg,
    so TfL and Google Routes routes look consistent.
    """
    if leg.mode == LegMode.WALK:
        if leg.end_station:
            return f"walk to {Station.short_name(leg.end_station)}"
        return "walk"
    if leg.line_name and leg.end_station:
        return f"{leg.line_name} to {Station.short_name(leg.end_station)}"
    if leg.line_name:
        return leg.line_name
    if leg.end_station:
        return f"{leg.mode.name.lower()} to {Station.short_name(leg.end_station)}"
    return leg.mode.name.lower()


class LegMode(Enum):
    WALK = auto()
    TUBE = auto()
    BUS = auto()
    TRAIN = auto()
    DLR = auto()
    OVERGROUND = auto()
    TRAM = auto()
    DRIVE = auto()
    CYCLE = auto()
    PARK = auto()


class CommuteMode(Enum):
    TRANSIT = auto()
    DRIVE = auto()


@dataclass(frozen=True)
class JourneyLeg:
    """One segment of a commute journey."""

    mode: LegMode
    duration_minutes: int
    start_station: str = ""  # departure point name from TfL
    end_station: str = ""  # arrival point name from TfL
    line_name: str = ""  # transit route name from TfL (e.g. "Bakerloo", "Great Western Railway")


@dataclass(frozen=True)
class CostGroup:
    """A contiguous set of legs priced as a single unit, by one operator.

    One TfL tap-in/tap-out covers tube→walk→tube as one CostGroup.
    A boring CostGroup (walking to/from transit) has no operator and no cost.
    """

    legs: tuple[JourneyLeg, ...]
    operator: str = ""
    cost: Money | float | None = None  # None = free (walking).  Parking CostGroups use Money.

    def leg_descriptions(self) -> tuple[str, ...]:
        """Return operator-appropriate descriptions for each leg."""
        return tuple(_render_leg_description(leg) for leg in self.legs)


@dataclass(frozen=True)
class Commute:
    """A person's journey between home and a destination."""

    destination_label: str
    destination_postcode: str
    duration_minutes: int | None = None
    daily_cost_gbp: Money | None = None
    mode: str | CommuteMode = "transit"
    cost_groups: tuple[CostGroup, ...] = ()

    def _leg_description(self, leg: JourneyLeg) -> str:
        """Build a human-readable leg description from raw fields."""
        return _render_leg_description(leg)

    def summary(self) -> str:
        """Render as the sheet's route-summary string."""
        parts: list[str] = []
        all_legs = [leg for group in self.cost_groups for leg in group.legs]
        total = len(all_legs)

        for idx, leg in enumerate(all_legs):
            desc = self._leg_description(leg)
            if leg.mode == LegMode.WALK and idx == total - 1:
                parts.append(f"walk {leg.duration_minutes}m")
            else:
                parts.append(f"{desc} ({leg.duration_minutes}m)")

        return " \u2192 ".join(parts)

    def non_rail_cost(self) -> float:
        """Sum of costs from non-TfL cost groups (bus, parking, etc.).

        Parking costs are stored as ``Money`` objects to avoid float
        precision artifacts; bus and TfL costs are stored as plain floats.
        """
        total = 0.0
        for cg in self.cost_groups:
            if cg.cost is not None and cg.operator != "TfL":
                if isinstance(cg.cost, Money):
                    total += float(cg.cost.amount)
                else:
                    total += cg.cost
        return total


@dataclass(frozen=True)
class CommuteBreakdown:
    """Individual daily costs plus yearly total."""

    simon_daily_gbp: float | None = None
    lorena_daily_gbp: float | None = None
    bracknell_daily_gbp: float | None = None
    yearly_total_gbp: float | None = None
    formula_explanation: str = ""
