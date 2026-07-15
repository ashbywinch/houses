from __future__ import annotations

from pydantic import TypeAdapter

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from houses.model.domain import Commute


class BusRouteNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_location, walk_leg_check_node, transit_node):
        super().__init__(node_id, dict, (best_location, walk_leg_check_node, transit_node))

    def compute(self, location: Attempt, walk_check: Attempt, transit: Attempt) -> Attempt[dict]:
        # Simplified — returns route info
        if not location.succeeded:
            return self._impossible({"best_location": location})
        return Attempt.succeeded({"route": "simplified"})


class BodsFareNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, bus_route_node):
        super().__init__(node_id, dict, (bus_route_node,))

    def compute(self, route: Attempt[dict]) -> Attempt[dict]:
        if not route.succeeded:
            return self._impossible({"bus_route_node": route})
        return Attempt.succeeded({"fare": 0})


class BusLegAugmentNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, transit_node, walk_leg_check_node, bus_route_node, bods_fare_node):
        super().__init__(node_id, dict, (transit_node, walk_leg_check_node, bus_route_node, bods_fare_node))

    def compute(self, transit: Attempt, walk_check: Attempt, route: Attempt, fare: Attempt) -> Attempt[dict]:
        if transit.succeeded:
            # TransitNode now returns Commute; convert to dict for persistence
            val = transit.value_or_none()
            if isinstance(val, Commute):
                return Attempt.succeeded(TypeAdapter(Commute).dump_python(val, mode="json"))
            return transit
        if not walk_check.succeeded or not route.succeeded:
            return self._impossible({"walk_leg_check_node": walk_check, "bus_route_node": route})
        return Attempt.succeeded({"augmented": True})
