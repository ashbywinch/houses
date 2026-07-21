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

# Register pydantic schema for TypeAdapter serialization
from pydantic_core import core_schema  # noqa: E402 — pydantic registration after class def

if not hasattr(LegMode, "__get_pydantic_core_schema__"):
    def _legmode_schema(_source, _handler):
        def validate(v):
            if isinstance(v, LegMode):
                return v
            if isinstance(v, str):
                return LegMode[v.upper()]
            if isinstance(v, int):
                return LegMode(v)
            raise ValueError(f"Cannot convert {type(v)} to LegMode")
        def serialize(lm):
            return lm.name.lower()
        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(serialize),
        )
    LegMode.__get_pydantic_core_schema__ = _legmode_schema


class CommuteMode(Enum):
    TRANSIT = auto()
    DRIVE = auto()


@dataclass(frozen=True)
class JourneyLeg:
    """One segment of a commute journey."""

    mode: LegMode
    duration_minutes: int
    distance_km: float = 0.0
    start_station: str = ""
    end_station: str = ""
    line_name: str = ""


@dataclass(frozen=True)
class CostGroup:
    """A contiguous set of legs priced as a single unit, by one operator.

    One TfL tap-in/tap-out covers tube→walk→tube as one CostGroup.
    An NR ticket covering train→tube is another CostGroup.

    ``cost`` is the price of the WHOLE group — a single product from one
    operator.  NEVER add to an existing CostGroup's cost; create a new
    CostGroup for each separately-priced product.  The commute's total
    cost is the SUM of all its CostGroups' costs.
    """

    legs: tuple[JourneyLeg, ...]
    operator: str = ""
    cost: Money | float | None = None  # None = free (walking).  Parking CostGroups use Money.

    def leg_descriptions(self) -> tuple[str, ...]:
        """Return operator-appropriate descriptions for each leg."""
        return tuple(_render_leg_description(leg) for leg in self.legs)




@dataclass(frozen=True)
class CommuteBreakdown:
    """Individual daily costs plus yearly total."""

    simon_daily_gbp: float | None = None
    lorena_daily_gbp: float | None = None
    bracknell_daily_gbp: float | None = None
    yearly_total_gbp: float | None = None
    formula_explanation: str = ""
