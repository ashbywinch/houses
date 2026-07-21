from __future__ import annotations

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from houses.model.domain import Commute


class BusRouteNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_location, walk_leg_check_node, transit_node):
        super().__init__(node_id, dict, (best_location, walk_leg_check_node, transit_node))

    def compute(self, location: Attempt, walk_check: Attempt, transit: Attempt) -> Attempt[dict]:
        # Simplified — returns route info
        return Attempt.succeeded({"route": "simplified"})


class BodsFareNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, bus_route_node):
        super().__init__(node_id, dict, (bus_route_node,))

    def compute(self, route: Attempt[dict]) -> Attempt[dict]:
        return Attempt.succeeded({"fare": 0})


class BusLegAugmentNode(DerivedNode[Commute]):
    def __init__(self, node_id: str, *, transit_node, walk_leg_check_node, bus_route_node, bods_fare_node):
        super().__init__(node_id, Commute, (transit_node, walk_leg_check_node, bus_route_node, bods_fare_node))

    def compute(self, transit: Attempt, walk_check: Attempt, route: Attempt, fare: Attempt) -> Attempt[Commute]:
        if transit.succeeded:
            val = transit.value_or_none()
            if not isinstance(val, Commute):
                return transit
            # Apply BODS fare to bus CostGroups
            fr = fare.value_or_none() if fare.succeeded else {}
            new_details = list(val.details or ())
            changed = False
            from dataclasses import replace

            from money import Money

            for i, cg in enumerate(new_details):
                bus_legs = [leg for leg in cg.legs if leg.mode.name == "BUS"]
                if not bus_legs:
                    continue
                stop_name = bus_legs[0].start_station
                stop_data = (fr or {}).get(stop_name, {})
                bus_fare = stop_data.get("single_fare") if isinstance(stop_data, dict) else None
                if bus_fare is not None:
                    new_details[i] = replace(cg, cost=Money(str(bus_fare), "GBP"))
                    changed = True
            if changed:
                new_commute = replace(val, details=tuple(new_details))
                return Attempt.succeeded(new_commute)
            return Attempt.succeeded(val)
        if not walk_check.succeeded or not route.succeeded:
            return self._impossible({"walk_leg_check_node": walk_check, "bus_route_node": route})
        return Attempt.impossible("no transit or bus route")
