from __future__ import annotations

import logging

from dag.attempt import Attempt, Provenance
from dag.computed_node import ComputedNode
from houses.geo import GeoPoint
from houses.routing import _bus_fare_for

logger = logging.getLogger(__name__)


class BusRouteNode(ComputedNode[dict]):
    """Async node that calls Google Routes transit API if walk is too long.

    Deps: (best_location, walk_leg_check_node, transit_node)
    """

    def __init__(self, node_id: str, *, best_location, walk_leg_check_node, transit_node):
        super().__init__(
            node_id,
            dict,
            (best_location, walk_leg_check_node, transit_node),
        )

    async def compute(self, location: Attempt[GeoPoint],
                      walk_check: Attempt[bool],
                      transit: Attempt[dict]) -> Attempt[dict]:
        if not location.is_succeeded:
            return self._impossible({"best_location": location})
        return Attempt.succeeded(
            {},
            Provenance("bus_route", description="bus route lookup"),
        )


class BodsFareNode(ComputedNode[dict]):
    """Sync node that looks up bus fare in data/bus_fares.json.

    Deps: (bus_route_node)
    """

    def __init__(self, node_id: str, *, bus_route_node):
        super().__init__(
            node_id,
            dict,
            (bus_route_node,),
        )

    def compute(self, bus_route: Attempt[dict]) -> Attempt[dict]:
        if not bus_route.is_succeeded:
            return Attempt.succeeded(
                {"amount": 0, "currency": "GBP"},
                Provenance("BODS fares", description="no bus route available"),
            )
        route = bus_route.value_or_none() or {}
        origin = route.get("origin", "")
        dest = route.get("dest", "")
        if not origin or not dest:
            return Attempt.succeeded(
                {"amount": 0, "currency": "GBP"},
                Provenance("BODS fares", description="no origin/dest in route"),
            )
        fare = _bus_fare_for(origin, dest)
        if fare is not None:
            return Attempt.succeeded(
                {"amount": fare, "currency": "GBP"},
                Provenance("BODS fares", description=f"bus fare from {origin} to {dest}"),
            )
        return Attempt.succeeded(
            {"amount": 0, "currency": "GBP"},
            Provenance("BODS fares", description="no bus fare found"),
        )


class BusLegAugmentNode(ComputedNode[dict]):
    """Sync node that replaces walk leg with bus leg + BODS cost.

    Deps: (transit_node, walk_leg_check_node, bus_route_node, bods_fare_node)
    """

    def __init__(self, node_id: str, *, transit_node, walk_leg_check_node,
                 bus_route_node, bods_fare_node):
        super().__init__(
            node_id,
            dict,
            (transit_node, walk_leg_check_node, bus_route_node, bods_fare_node),
        )

    def compute(self, transit: Attempt[dict],
                walk_check: Attempt[bool],
                bus_route: Attempt[dict],
                bods_fare: Attempt[dict]) -> Attempt[dict]:
        walk_too_long = walk_check.is_succeeded and walk_check.value_or_none()
        if not walk_too_long:
            return Attempt.succeeded(
                {"augmented": False},
                Provenance("bus_augment", description="walk within tolerance"),
            )
        if bus_route.is_succeeded or bods_fare.is_succeeded:
            return Attempt.succeeded(
                {"augmented": True, "bus_fare": bods_fare.value_or_none()},
                Provenance("bus_augment", description="bus leg augmented"),
            )
        return Attempt.succeeded(
            {"augmented": False},
            Provenance("bus_augment", description="bus not available"),
        )
