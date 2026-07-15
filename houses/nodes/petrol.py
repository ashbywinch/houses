"""DAG node that adds fuel costs for drive-leg commutes.

Uses actual distance (``distance_km``) from drive legs when available,
otherwise estimating from drive minutes at 48 km/h.  Reads petrol_mpg
and petrol_cost_per_litre from the financial settings UserInputNode so
the user can adjust values live.
"""

from __future__ import annotations

from dataclasses import replace

from money import Money

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
        if not val or val.mode != "drive":
            return commute  # only applies to drive commutes

        # Collect all drive legs
        drive_legs = [leg for cg in val.details for leg in cg.legs if leg.mode == LegMode.DRIVE]
        if not drive_legs:
            return commute

        # Get settings from financial source (user-editable)
        fin = financial.value_or_none() if (financial and financial.succeeded) else {}
        mpg = float(fin.get("petrol_mpg", 45))
        cost_per_litre = float(fin.get("petrol_cost_per_litre", 1.45))

        # Use actual distance_km when available (>0), otherwise estimate from minutes
        actual_distance = sum(leg.distance_km for leg in drive_legs if leg.distance_km > 0)
        if actual_distance > 0:
            # Round trip — commute shows one way, costs are daily (round trip)
            round_trip_km = actual_distance * 2
        else:
            total_drive_min = sum(leg.duration_minutes for leg in drive_legs)
            if total_drive_min <= 0:
                return commute
            # Estimate distance: 48 km/h average speed, round trip
            round_trip_km = (total_drive_min / 60.0) * 48.0 * 2

        # Fuel calculation:
        #   litres/100km = 235.214 / mpg
        #   litres = (distance_km / 100) * litres_per_100km
        litres_used = (round_trip_km / 100.0) * (235.214 / mpg)
        fuel_cost = round(litres_used * cost_per_litre, 2)

        if fuel_cost <= 0:
            return commute

        new_commute = replace(
            val,
            daily_cost=Money(str(round(float(val.daily_cost.amount) + fuel_cost, 2)), "GBP"),
        )
        return Attempt.succeeded(new_commute)
