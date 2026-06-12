"""Commute value objects — journey legs, cost groups, and commute results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from houses.stations import Station


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
    description: str = ""  # e.g. "walk to Maidenhead" or "Bakerloo line to Oxford Circus"
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
    cost: float | None = None  # None = free (walking)

    def leg_descriptions(self) -> tuple[str, ...]:
        """Return operator-appropriate descriptions for each leg."""
        return tuple(leg.description if leg.description else leg.mode.name.lower() for leg in self.legs)


@dataclass(frozen=True)
class Commute:
    """A person's journey between home and a destination."""

    destination_label: str
    destination_postcode: str
    duration_minutes: int | None = None
    daily_cost_gbp: float | None = None
    mode: str | CommuteMode = "transit"
    cost_groups: tuple[CostGroup, ...] = ()

    def summary(self) -> str:
        """Render as the sheet's route-summary string."""
        parts: list[str] = []
        all_legs = [leg for group in self.cost_groups for leg in group.legs]
        total = len(all_legs)

        for idx, (_group, leg, desc) in enumerate(
            (g, _leg, d) for g in self.cost_groups for _leg, d in zip(g.legs, g.leg_descriptions(), strict=True)
        ):
            if leg.mode == LegMode.WALK:
                if idx == total - 1:
                    parts.append(f"walk {leg.duration_minutes}m")
                elif leg.end_station:
                    parts.append(f"walk to {Station.short_name(leg.end_station)} ({leg.duration_minutes}m)")
                else:
                    parts.append(f"{desc} ({leg.duration_minutes}m)")
            else:
                parts.append(f"{desc} ({leg.duration_minutes}m)")

        return " \u2192 ".join(parts)

    def non_rail_cost(self) -> float:
        """Sum of costs from non-TfL cost groups (bus, parking, etc.)."""
        total = 0.0
        for cg in self.cost_groups:
            if cg.cost is not None and cg.operator != "TfL":
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
