"""DAG node that adds fuel costs for drive-leg commutes.

Uses actual distance (``distance_km``) from drive legs when available,
otherwise estimating from drive minutes at 48 km/h.  Reads petrol_mpg
and petrol_cost_per_litre from individual setting nodes.
"""

from __future__ import annotations

from dataclasses import replace

from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from houses.commute import LegMode
from houses.model.domain import Commute


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

    def compute(self, commute: Attempt[Commute], mpg_att: Attempt, cost_att: Attempt) -> Attempt[Commute]:
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

        actual_distance = sum(
            leg.distance.magnitude for leg in drive_legs if leg.distance and leg.distance.magnitude > 0
        )
        if actual_distance > 0:
            round_trip_km = actual_distance * 2
        else:
            total_drive_min = sum(int(leg.duration.magnitude) for leg in drive_legs)
            if total_drive_min <= 0:
                return commute
            round_trip_km = (total_drive_min / 60.0) * 48.0 * 2

        # MPG -> litres per 100km: (100 * 4.54609) / (mpg * 1.60934) ≈ 282.5 / mpg
        litres_per_100km = 282.5 / mpg if mpg > 0 else 10.0
        fuel_used_litres = round_trip_km / litres_per_100km
        daily_fuel_cost = fuel_used_litres * cost_per_litre
        daily_cost = val.daily_cost + Money(str(round(daily_fuel_cost, 2)), "GBP")

        new_commute = replace(val, daily_cost=daily_cost)
        return Attempt.succeeded(new_commute)

    async def to_json(self) -> dict:
        result = await super().to_json()
        attempt = await self.attempt()
        if attempt.succeeded:
            val = attempt.value_or_none()
            if val is not None:
                result["is_child"] = val.is_child
        return result

    async def to_json_value(self) -> dict:
        result = await super().to_json_value()
        attempt = await self.attempt()
        if attempt.succeeded:
            val = attempt.value_or_none()
            if val is not None:
                result["is_child"] = val.is_child
        return result
