"""Replace a too-long walk leg with a bus leg (or the whole journey)."""

from __future__ import annotations

from dataclasses import replace
from typing import override

from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.model.domain import Commute
from houses.settings import settings


class BusLegAugmentNode(DerivedNode[Commute]):
    """Replace the first walk leg with a bus leg when the walk is too long.

    Park-and-ride and bus-augment are alternatives for the same problem
    (long walk to station). If park-and-ride already replaced the walk
    (has_car=True, parking found), this node is a no-op.
    """

    def __init__(
        self,
        node_id: str,
        *,
        transit_input,  # park_and_ride result or raw transit
        bus_route_node,
        bods_fare_node,
        max_walk_node=None,
    ):
        self._transit_input: Node = transit_input
        self._bus_route_node: Node = bus_route_node
        self._bods_fare_node: Node = bods_fare_node
        self._max_walk_node: Node | None = max_walk_node
        deps = [transit_input]
        if max_walk_node is not None:
            deps.append(max_walk_node)
        # Static deps include the bus route + fare nodes so their changed
        # signals re-schedule this node when they resolve LATER — a refresh
        # that found them pending must not leave the augment stuck forever.
        # _get_active_deps gates whether a pending/irrelevant bus route can
        # block (same pattern as ParkAndRideAugmentNode's postcode).
        deps.append(bus_route_node)
        deps.append(bods_fare_node)
        names = ["transit_attempt"]
        if max_walk_node is not None:
            names.append("max_walk")
        names += ["bus_route_attempt", "bods_fare_attempt"]
        super().__init__(
            node_id,
            Commute,
            tuple(deps),
            dep_names=tuple(names),
        )

    def _current_max_walk(self) -> int:
        """The effective walk tolerance — staging-aware, so a what-if
        max-walk change re-gates the bus augmentation with the candidate
        value even though the planned routes are unchanged."""
        att = self._max_walk_node.latest_attempt() if self._max_walk_node else None
        val = att.value_or_none() if att is not None else None
        return int(val) if val is not None else 30

    @override
    def _get_active_deps(self):
        """Only need bus route and fare when the walk is too long, or when
        TfL found no route at all (the Google Routes bus is the fallback)."""
        deps = [self._transit_input]
        if self._max_walk_node is not None:
            deps.append(self._max_walk_node)
        transit_attempt = self._transit_input.latest_attempt()
        if transit_attempt.succeeded:
            val = transit_attempt.value_or_none()
            if val is not None and (val.infeasible or self._walk_too_long(val)):
                deps.append(self._bus_route_node)
                deps.append(self._bods_fare_node)
        return tuple(deps)

    def _walk_too_long(self, commute: Commute, max_walk_value: int | None = None) -> bool:
        if commute.infeasible:
            # .details raises on infeasible commutes — and an infeasible
            # route has no walk leg to augment anyway.
            return False
        if not commute.details:
            return False
        first_legs = commute.details[0].legs
        if not first_legs:
            return False
        if first_legs[0].mode != LegMode.WALK:
            return False
        limit = max_walk_value if max_walk_value is not None else self._current_max_walk()
        return int(first_legs[0].duration.magnitude) > limit

    @override
    def compute(
        self,
        transit_attempt: Attempt[Commute],
        max_walk: Attempt[int] | None = None,
        bus_route_attempt: Attempt[dict] | None = None,
        bods_fare_attempt: Attempt[dict] | None = None,
    ) -> Attempt[Commute]:
        if not transit_attempt.succeeded:
            return transit_attempt

        commute = transit_attempt.value_or_none()
        if commute is None:
            return transit_attempt

        if commute.infeasible:
            # TfL has no route — fall back to a full Google Routes bus
            # journey, using the same bus-building logic as the
            # walk-replacement path below.
            return self._bus_augment(commute, bus_route_attempt, bods_fare_attempt, full_trip=True)

        if not self._walk_too_long(commute, max_walk.value_or_none() if max_walk is not None else None):
            return Attempt.succeeded(commute)
        return self._bus_augment(commute, bus_route_attempt, bods_fare_attempt, full_trip=False)

    @staticmethod
    def _bus_augment(
        commute: Commute,
        bus_route_attempt: Attempt[dict] | None,
        bods_fare_attempt: Attempt[dict] | None,
        *,
        full_trip: bool,
    ) -> Attempt[Commute]:
        """Build the bus-augmented commute from a Google Routes bus result.

        ``full_trip`` (TfL no-route fallback): the bus is the entire
        journey — duration and details come from the route alone.
        Otherwise the bus replaces the first (too-long) walk leg and the
        savings-vs-walk penalty check applies, as for adult commutes.
        """
        if not bus_route_attempt or not bus_route_attempt.succeeded:
            return Attempt.succeeded(commute)

        if not bods_fare_attempt or not bods_fare_attempt.succeeded:
            return Attempt.succeeded(commute)

        route_val = bus_route_attempt.value_or_none()
        fare_val = bods_fare_attempt.value_or_none()
        if not route_val or not fare_val:
            return Attempt.succeeded(commute)

        bus_stops = route_val.get("bus_stops", [])
        if not bus_stops:
            return Attempt.succeeded(commute)

        stop_fares = fare_val.get("stop_fares", {})
        bus_time = route_val.get("duration_minutes", 0)
        if bus_time <= 0:
            return Attempt.succeeded(commute)

        # Compute bus cost from stop fares
        total_bus_cost = Money(amount="0", currency="GBP")
        for stop in bus_stops:
            dep = stop.get("departure_name", "")
            fare_info = stop_fares.get(dep)
            if fare_info:
                total_bus_cost += Money(str(fare_info["amount"]), fare_info["currency"])

        if full_trip:
            walk_minutes = 0
            rest_details: tuple = ()
        else:
            walk_minutes = int(commute.details[0].legs[0].duration.magnitude)
            rest_details = commute.details[1:]
            penalty = int(settings.bus_walk_penalty.magnitude)
            savings = walk_minutes - bus_time
            if savings < penalty:
                return Attempt.succeeded(commute)

        # Build the bus CostGroup
        bus_cg = CostGroup(
            legs=(JourneyLeg(mode=LegMode.BUS, duration=Quantity(bus_time, "minute")),),
            cost=total_bus_cost if total_bus_cost > Money(amount="0", currency="GBP") else None,
        )

        new_duration = int(commute.duration.magnitude - walk_minutes + bus_time)
        new_daily_cost = commute.daily_cost + total_bus_cost

        new_commute = replace(
            commute,
            duration=Quantity(new_duration, "minute"),
            daily_cost=new_daily_cost,
            _details=(bus_cg,) + rest_details,
            infeasible=False if full_trip else commute.infeasible,
        )
        return Attempt.succeeded(new_commute)
