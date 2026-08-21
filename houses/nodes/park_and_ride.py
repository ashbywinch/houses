"""DAG node that replaces long walks with drive + park.

When the traveler has a car and the first-leg walk exceeds their max_walk
threshold, this node replaces the walking leg with a driving leg (using
actual drive time from OpenRouteService) and adds the parking cost for
the station's car park.
"""

from __future__ import annotations

from dataclasses import replace
from typing import override

from money import Money
from pint import Quantity

from dag.attempt import Attempt, Provenance, SourceType
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.car_park import CarParkRegistry
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.geo import GeoPoint
from houses.model.domain import Commute
from houses.stations import StationRegistry


class ParkAndRideAugmentNode(DerivedNode[Commute]):
    """Augments a transit commute with park-and-ride (drive to station, park, ride transit).

    Depends on the transit result, the property's location (for station lookups),
    and — when known — the property's postcode (for drive time estimation).

    The postcode is a CONDITIONAL dependency: a pending/empty postcode
    must not stall the chain, so ``_get_active_deps`` excludes it until
    it carries a value, and compute falls back to the best location for
    the drive-time estimate.  When the postcode later resolves, the
    static-dep signal re-schedules this node and the estimate upgrades
    to the postcode-based one.
    """

    def __init__(
        self,
        node_id: str,
        *,
        transit_node: Node,
        best_location: Node,
        postcode_node: Node,
        has_car: bool,
        max_walk_node,
        station_registry: StationRegistry | None = None,
        car_park_registry: CarParkRegistry | None = None,
    ):
        self.transit_node = transit_node
        self.best_location = best_location
        self.postcode_node = postcode_node
        self._has_car = has_car
        self._max_walk = 30
        self._max_walk_node = max_walk_node
        self._car_park_name: str = ""
        self._station_registry = station_registry
        self._car_park_registry = car_park_registry
        # Static deps include postcode so its changed signal re-schedules
        # this node when a postcode arrives later; _get_active_deps gates
        # whether a PENDING postcode can block refresh.
        deps = (transit_node, max_walk_node)
        names = ["transit", "max_walk"]
        if has_car:
            deps = deps + (best_location, postcode_node)
            names += ["location", "postcode_attempt"]
        super().__init__(node_id, Commute, deps, dep_names=tuple(names))
        self.display_name = "Park & Ride"

    @override
    def _get_active_deps(self):
        """The postcode is only an active dep once it has a value — a
        pending/empty postcode must not stall refresh (a permanently
        pending UserInputNode with no producer would freeze every
        has-car commute chain forever)."""
        deps: list[Node] = [self.transit_node, self._max_walk_node]
        if self._has_car:
            deps.append(self.best_location)
            pc = self.postcode_node.latest_attempt()
            if pc.succeeded and pc.value_or_none():
                deps.append(self.postcode_node)
        return tuple(deps)

    @override
    async def compute(
        self,
        transit: Attempt[Commute],
        max_walk: Attempt[int] | None = None,
        location: Attempt[GeoPoint] | None = None,
        postcode_attempt: Attempt[str] | None = None,
    ) -> Attempt[Commute]:
        mw_val = max_walk.value_or_none() if max_walk is not None else None
        if mw_val is not None:
            self._max_walk = int(mw_val)

        commute = transit.value_or_none()
        if commute is None:
            return transit
        if commute.infeasible:
            # No route — pass the infeasible commute through unchanged;
            # .details raises on infeasible commutes, so bail before touching it.
            return transit

        # Only consider commutes where the first leg is walking
        if not commute.details or not commute.details[0].legs:
            return transit
        if commute.details[0].legs[0].mode != LegMode.WALK:
            return transit

        walk_min = int(commute.details[0].legs[0].duration.magnitude)

        # If the walk is acceptable, no parking needed
        if walk_min <= self._max_walk:
            return transit

        # Without a car, park-and-ride isn't an option
        if not self._has_car:
            return transit

        # Find the station at the end of the walk leg
        station_name = commute.details[0].legs[0].end_station
        if not station_name:
            return transit

        # Drive time needs either a postcode or the best location.
        # With neither, the walk stays — park-and-ride is skipped, but
        # the commute itself is still valid.
        postcode = postcode_attempt.value_or_none() if postcode_attempt and postcode_attempt.succeeded else None
        origin = location.value_or_none() if location and location.succeeded else None
        if not postcode and origin is None:
            return transit

        # Get actual drive time via the drive time service — postcode
        # first, best-location fallback when the property has none.
        from houses.services_provider import get_services

        if postcode:
            drive_minutes = await get_services().drive_time_service.estimate(postcode, station_name)
        else:
            drive_minutes = await get_services().drive_time_service.estimate_from_location(origin, station_name)
        if drive_minutes is None:
            return transit

        # Look up car park cost at that station (injected or default)
        registry = self._station_registry or StationRegistry()
        station = registry.find(station_name)
        if station is None:
            return transit

        parking = self._car_park_registry or CarParkRegistry()
        car_park = parking.find_car_park(station)
        # The drive leg is added whenever driving beats walking — a
        # missing car-park COST must not leave a 76-minute walk in place.
        # The PARK leg is shown whenever a car park exists (its name
        # identifies the park-and-ride); the cost is only attached when
        # known.
        parking_cost = car_park.daily_cost if car_park is not None else None
        existing_cost = commute.daily_cost
        if existing_cost is None:
            existing_cost = Money("0", "GBP")

        # Replace the walk leg with a drive leg (actual drive time).
        # The drive leg goes in its OWN CostGroup so that fuel cost and
        # rail fare can be attributed independently.
        first_cg = commute.details[0]
        first_leg = first_cg.legs[0]
        new_drive_leg = replace(first_leg, mode=LegMode.DRIVE, duration=Quantity(drive_minutes, "minute"))
        new_drive_group = CostGroup(
            legs=(new_drive_leg,),
            cost=None,
        )
        # Remaining transit legs (train/tube) stay in their own CostGroup
        # with the original operator and cost.
        transit_legs = first_cg.legs[1:]
        if car_park is not None:
            new_parking_group = CostGroup(
                legs=(JourneyLeg(mode=LegMode.PARK, duration=Quantity(0, "minute")),),
                operator=car_park.name,
                cost=parking_cost,
            )
            new_cost = existing_cost + parking_cost if parking_cost is not None else existing_cost
        else:
            new_parking_group = None
            new_cost = existing_cost
        # If the first CostGroup had transit legs after the walk, keep them in
        # their own group; otherwise there's only the drive group + parking.
        if transit_legs:
            new_transit_group = replace(first_cg, legs=transit_legs)
            # Attribute the original transit fare if no cost yet
            if new_transit_group.cost is None:
                has_transit = any(leg.mode in (LegMode.TRAIN, LegMode.TUBE, LegMode.BUS) for leg in transit_legs)
                if has_transit:
                    new_transit_group = replace(new_transit_group, cost=existing_cost)
            tail = commute.details[1:]
            if new_parking_group is not None:
                new_details = (new_drive_group, new_parking_group, new_transit_group) + tail
            else:
                new_details = (new_drive_group, new_transit_group) + tail
        else:
            tail = commute.details[1:]
            if new_parking_group is not None:
                new_details = (new_drive_group, new_parking_group) + tail
            else:
                new_details = (new_drive_group,) + tail
        # Recalculate duration from replaced legs
        new_duration = Quantity(sum(int(leg.duration.magnitude) for cg in new_details for leg in cg.legs), "minute")
        new_commute = replace(
            commute,
            daily_cost=new_cost,
            _details=new_details,
            duration=new_duration,
        )

        self._car_park_name = (car_park.name or "unknown car park") if car_park is not None else "unknown car park"
        return Attempt.succeeded(new_commute)

    @override
    async def build_provenance(self) -> Provenance:
        sources: dict[str, Provenance] = {}
        for dep in self._get_active_deps():
            sources[dep._id] = await dep.build_provenance()
        label = self.display_name
        description = f"parking at {self._car_park_name}" if self._car_park_name else ""
        return Provenance(
            label=label,
            description=description,
            source_type=SourceType.CALC,
            freshness=self._attempt.created_at,
            sources=sources,
        )
