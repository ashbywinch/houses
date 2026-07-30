"""DAG node that adds fuel costs for drive-leg commutes.

Uses actual distance (``distance_km``) from drive legs when available,
otherwise estimating from drive minutes at 48 km/h.  Reads petrol_mpg
and petrol_cost_per_litre from the financial settings UserInputNode so
the user can adjust values live.
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

    Prefers actual ``distance_km`` from each drive leg (set by the transit
    router from Google Routes data).  Falls back to estimating distance
    from total drive minutes at 48 km/h (for legs where the router
    didn't provide distance, e.g. park-and-ride drive legs).
    """

    def __init__(self, node_id: str, *, commute_node, financial_source):
        self.commute_node = commute_node
        self.financial_source = financial_source
        deps = (commute_node, financial_source)
        super().__init__(node_id, Commute, deps)
        self.display_name = "Petrol Cost"

    def compute(self, commute: Attempt[Commute], financial: Attempt[dict] = None) -> Attempt[Commute]:
        if not commute.succeeded:
            return commute
        val = commute.value_or_none()
        if not val:
            return commute

        # Check for drive legs (handles park-and-ride which has mixed details)
        drive_groups = [cg for cg in (val.details or ()) if any(leg.mode == LegMode.DRIVE for leg in cg.legs)]
        if not drive_groups:
            return commute  # no drive legs to add fuel cost to

        # Collect all drive legs
        drive_legs = [leg for cg in val.details for leg in cg.legs if leg.mode == LegMode.DRIVE]
        if not drive_legs:
            return commute
        import logging

        logger = logging.getLogger(__name__)
        logger.debug(
            "PETROL_DEBUG: %d drive legs, total_min=%d, distances=%s",
            len(drive_legs),
            sum(int(leg.duration.magnitude) for leg in drive_legs),
            [leg.distance.magnitude if leg.distance else 0 for leg in drive_legs],
        )

        # Get settings from financial source (user-editable)
        fin = financial.value_or_none() if (financial and financial.succeeded) else {}
        mpg = float(fin.get("petrol_mpg", 45))
        cost_per_litre = float(fin.get("petrol_cost_per_litre", 1.45))

        # Use actual distance_km when available (>0), otherwise estimate from minutes
        actual_distance = sum(
            leg.distance.magnitude for leg in drive_legs if leg.distance and leg.distance.magnitude > 0
        )
        if actual_distance > 0:
            # Round trip — commute shows one way, costs are daily (round trip)
            round_trip_km = actual_distance * 2
        else:
            total_drive_min = sum(int(leg.duration.magnitude) for leg in drive_legs)
            if total_drive_min <= 0:
                return commute
            # Estimate distance: 48 km/h average speed, round trip
            round_trip_km = (total_drive_min / 60.0) * 48.0 * 2

        # Fuel calculation using pint for proper Imperial gallon -> litre conversion
        # 1 imperial gallon = 4.54609 litres; US gallon is 3.78541 litres
        fuel_volume = (Quantity(round_trip_km, "km") / Quantity(mpg, "mile / imperial_gallon")).to("liter")
        fuel_cost_amount = round(float(fuel_volume.magnitude) * cost_per_litre, 2)

        if fuel_cost_amount <= 0:
            return commute

        fuel_cost = Money(str(fuel_cost_amount), "GBP")
        new_daily_cost = val.daily_cost + fuel_cost

        # Attribute fuel cost to the drive CostGroup(s)
        new_details = list(val.details)

        for i, cg in enumerate(new_details):
            has_drive = any(leg.mode == LegMode.DRIVE for leg in cg.legs)
            if has_drive:
                if cg.cost is None:
                    new_cg_cost = fuel_cost
                else:
                    if not isinstance(cg.cost, Money):
                        raise TypeError(
                            f"CostGroup.cost must be Money or None, got {type(cg.cost).__name__}: {cg.cost}"
                        )
                    new_cg_cost = cg.cost + fuel_cost
                new_details[i] = replace(cg, cost=new_cg_cost)
                break

        new_commute = replace(
            val,
            daily_cost=new_daily_cost,
            _details=tuple(new_details),
        )
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
        val = self._attempt.value_or_none() if self._attempt.succeeded else None
        if val is not None:
            result["is_child"] = val.is_child
        return result
