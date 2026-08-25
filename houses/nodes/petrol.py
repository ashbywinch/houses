"""DAG nodes for drive-leg fuel costs.

PetrolCostAugmentNode adds fuel costs using actual distance when
available, otherwise estimating from drive minutes at 48 km/h.  The
MPG is the car OWNER's own economy (PersonPetrolMpgNode reads the
persons node); petrol cost per litre is a market price from the
household finances.
"""

from __future__ import annotations

from dataclasses import replace
from typing import override

from money import Money
from pint import Quantity

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode
from houses.commute import JourneyLeg, LegMode
from houses.model.domain import Commute

MINUTES_PER_HOUR = 60.0
AVERAGE_DRIVE_SPEED_KMH = 48.0


def _fuel_cost_for(drive_legs: list[JourneyLeg], mpg: int, cost_per_litre: float) -> Money | None:
    """Compute the round-trip fuel cost for the given drive legs.

    Uses actual ``distance_km`` when present, else estimates distance
    from drive minutes at 48 km/h. Returns None when there is no
    measurable distance (no fuel cost to add).
    """
    actual_distance = sum(leg.distance.magnitude for leg in drive_legs if leg.distance and leg.distance.magnitude > 0)
    if actual_distance > 0:
        round_trip_km = actual_distance * 2
    else:
        total_drive_min = sum(int(leg.duration.magnitude) for leg in drive_legs)
        if total_drive_min <= 0:
            return None
        round_trip_km = (total_drive_min / MINUTES_PER_HOUR) * AVERAGE_DRIVE_SPEED_KMH * 2

    # Fuel calculation using pint for proper Imperial gallon -> litre conversion
    # 1 imperial gallon = 4.54609 litres; US gallon is 3.78541 litres
    fuel_volume = (Quantity(round_trip_km, "km") / Quantity(mpg, "mile / imperial_gallon")).to("liter")
    fuel_cost_amount = round(float(fuel_volume.magnitude) * cost_per_litre, 2)
    if fuel_cost_amount <= 0:
        return None
    return Money(str(fuel_cost_amount), "GBP")


class PersonPetrolMpgNode(DerivedNode[int]):
    """The car owner's own petrol economy (miles per imperial gallon).

    Reads the person from the settings persons node by name — changing
    the person's MPG in Settings flows into every drive commute without
    a rebuild (provenance shows the persons source).
    """

    def __init__(self, node_id: str, *, persons_source, person_name: str):
        self._person_name = person_name
        super().__init__(node_id, int, (persons_source,))
        self.display_name = "Petrol MPG"

    @override
    def compute(self, persons: Attempt[list]) -> Attempt[int]:
        if not persons.succeeded:
            return Attempt.impossible(persons.error)
        for p in persons.value_or_none() or []:
            if getattr(p, "name", None) == self._person_name:
                return Attempt.succeeded(int(getattr(p, "petrol_mpg", 45)))
        return Attempt.succeeded(45)


class PetrolCostAugmentNode(DerivedNode[Commute]):
    """Adds fuel cost for drive legs based on settings.

    Prefers actual ``distance_km`` from each drive leg. Falls back to
    estimating distance from total drive minutes at 48 km/h.
    """

    def __init__(self, node_id: str, *, commute_node, petrol_mpg_node, petrol_cost_per_litre_node):
        self.commute_node = commute_node
        deps = (commute_node, petrol_mpg_node, petrol_cost_per_litre_node)
        super().__init__(node_id, Commute, deps)
        self._mpg_node = petrol_mpg_node
        self._cost_node = petrol_cost_per_litre_node
        self.display_name = "Petrol Cost"

    @override
    @property
    def provenance_formula(self):

        commute = self._attempt.value_or_none()
        if not self._attempt.succeeded or commute is None:
            return None
        lines: list[FormulaLine] = []
        drive_legs = [leg for cg in commute.details for leg in cg.legs if leg.mode == LegMode.DRIVE]
        if drive_legs:
            actual = sum(leg.distance.magnitude for leg in drive_legs if leg.distance and leg.distance.magnitude > 0)
            if actual > 0:
                round_trip_km = actual * 2
                lines.append(FormulaLine(label="Drive distance (round trip)", value=f"{round_trip_km:.1f} km"))
            else:
                total_min = sum(int(leg.duration.magnitude) for leg in drive_legs)
                round_trip_km = (total_min / 60.0) * 48.0 * 2
                lines.append(FormulaLine(label="Drive time → distance estimate", value=f"{round_trip_km:.1f} km"))
            mpg = int(self._mpg_node.latest_attempt().value_or_none() or 45)
            cost = float(self._cost_node.latest_attempt().value_or_none() or 1.45)
            fuel = _fuel_cost_for(drive_legs, mpg, cost)
            if fuel is not None:
                lines.append(FormulaLine(label=f"Fuel: ÷ {mpg} mpg × £{cost}/litre", value=str(fuel)))
        if not lines:
            return None
        return Formula(lines=lines, result=str(commute.daily_cost))

    @override
    @staticmethod
    def compute(commute: Attempt[Commute], mpg_att: Attempt, cost_att: Attempt) -> Attempt[Commute]:
        if not commute.succeeded:
            return commute
        val = commute.value_or_none()
        if not val:
            return commute

        drive_groups = [cg for cg in (val.details or ()) if any(leg.mode == LegMode.DRIVE for leg in cg.legs)]
        if not drive_groups:
            return commute

        drive_legs = [leg for cg in val.details for leg in cg.legs if leg.mode == LegMode.DRIVE]
        if not drive_legs:
            return commute

        mpg = int(mpg_att.value_or_none() or 45)
        cost_per_litre = float(cost_att.value_or_none() or 1.45)

        fuel_cost = _fuel_cost_for(drive_legs, mpg, cost_per_litre)
        if fuel_cost is None:
            return commute

        new_daily_cost = val.daily_cost + fuel_cost

        # Attribute fuel cost to the drive CostGroup(s) so downstream
        # nodes (like MergeRailFareNode) that recompute total from
        # CostGroups don't lose the fuel addition.
        new_details = list(val.details)
        for i, cg in enumerate(new_details):
            has_drive = any(leg.mode == LegMode.DRIVE for leg in cg.legs)
            if has_drive:
                new_cg_cost = fuel_cost if cg.cost is None else cg.cost + fuel_cost
                new_details[i] = replace(cg, cost=new_cg_cost)
                break

        new_commute = replace(
            val,
            daily_cost=new_daily_cost,
            _details=tuple(new_details),
        )
        return Attempt.succeeded(new_commute)

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    @override
    async def to_json(self) -> dict:
        result = await super().to_json()
        attempt = await self.attempt()
        if attempt.succeeded:
            val = attempt.value_or_none()
            if val is not None:
                result["is_child"] = val.is_child
        return result

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    @override
    async def to_json_value(self) -> dict:
        result = await super().to_json_value()
        attempt = await self.attempt()
        if attempt.succeeded:
            val = attempt.value_or_none()
            if val is not None:
                result["is_child"] = val.is_child
        return result
