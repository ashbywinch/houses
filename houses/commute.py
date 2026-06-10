"""Commute value objects — journey legs, cost groups, and commute results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class LegMode(Enum):
    WALK = auto()
    TUBE = auto()
    BUS = auto()
    TRAIN = auto()
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
        return tuple(leg.mode.name.lower() for leg in self.legs)


@dataclass(frozen=True)
class Commute:
    """A person's journey between home and a destination."""

    destination_label: str
    destination_postcode: str
    duration_minutes: int | None = None
    daily_cost_gbp: float | None = None
    mode: str | CommuteMode = "transit"
    cost_groups: tuple[CostGroup, ...] = ()

    # Transitional fields — stay until cost_groups fully replaces them
    route_summary: str = ""
    parking_cost_gbp: float | None = None
    bus_cost_gbp: float | None = None

    def summary(self) -> str:
        """Render as the sheet's route-summary string."""
        if self.cost_groups:
            parts: list[str] = []
            for group in self.cost_groups:
                for leg, desc in zip(group.legs, group.leg_descriptions(), strict=True):
                    parts.append(f"{desc} ({leg.duration_minutes}m)")
            return " \u2192 ".join(parts)
        return self.route_summary


@dataclass(frozen=True)
class CommuteBreakdown:
    """Individual daily costs plus yearly total."""

    simon_daily_gbp: float | None = None
    lorena_daily_gbp: float | None = None
    bracknell_daily_gbp: float | None = None
    yearly_total_gbp: float | None = None
    formula_explanation: str = ""
