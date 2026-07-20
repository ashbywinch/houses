"""DAG node that replaces long walks with drive + park.

When the traveler has a car and the first-leg walk exceeds their max_walk
threshold, this node replaces the walking leg with a driving leg (using
actual drive time from OpenRouteService) and adds the parking cost for
the station's car park.
"""

from __future__ import annotations

from dataclasses import replace

from money import Money
from pint import Quantity

from dag.attempt import Attempt, Provenance
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
    and the property's postcode (for drive time estimation).
    """

    def __init__(
        self,
        node_id: str,
        *,
        transit_node: Node,
        best_location: Node,
        postcode_node: Node,
        has_car: bool,
        max_walk: int,
        station_registry: StationRegistry | None = None,
        car_park_registry: CarParkRegistry | None = None,
    ):
        self.transit_node = transit_node
        self.best_location = best_location
        self.postcode_node = postcode_node
        self._has_car = has_car
        self._max_walk = max_walk
        self._car_park_name: str = ""
        self._station_registry = station_registry
        self._car_park_registry = car_park_registry
        deps = (transit_node,)
        if has_car:
            deps = deps + (best_location, postcode_node)
        super().__init__(node_id, Commute, deps)
        self.display_name = "Park & Ride"

    async def compute(
        self, transit: Attempt[Commute], location: Attempt[GeoPoint] = None, postcode_attempt: Attempt[str] = None
    ) -> Attempt[Commute]:

        commute = transit.value_or_none()
        if commute is None:
            return transit

        # Only consider commutes where the first leg is walking
        if not commute.details or not commute.details[0].legs:
            return transit
        if commute.details[0].legs[0].mode != LegMode.WALK:
            return transit

        walk_min = commute.details[0].legs[0].duration_minutes

        # If the walk is acceptable, no parking needed
        if walk_min <= self._max_walk:
            return transit

        # Without a car, park-and-ride isn't an option
        if not self._has_car:
            return transit

        # Need postcode for drive time estimation
        postcode = postcode_attempt.value_or_none() if postcode_attempt and postcode_attempt.succeeded else None
        if not postcode:
            return transit

        # Find the station at the end of the walk leg
        station_name = commute.details[0].legs[0].end_station
        if not station_name:
            return transit

        # Get actual drive time via the drive time service
        from houses.services_provider import get_services

        drive_minutes = await get_services().drive_time_service.estimate(postcode, station_name)
        if drive_minutes is None:
            return transit

        # Look up car park cost at that station (injected or default)
        registry = self._station_registry or StationRegistry()
        station = registry.find(station_name)
        if station is None:
            return transit

        parking = self._car_park_registry or CarParkRegistry()
        car_park = parking.find_car_park(station)
        if car_park is None or car_park.daily_cost is None:
            return transit
        parking_cost = car_park.daily_cost
        existing_cost = commute.daily_cost
        if existing_cost is None:
            existing_cost = Money("0", "GBP")
        new_cost = existing_cost + parking_cost

        # Replace the walk leg with a drive leg (actual drive time)
        first_cg = commute.details[0]
        first_leg = first_cg.legs[0]
        new_first_leg = replace(first_leg, mode=LegMode.DRIVE, duration_minutes=drive_minutes)
        new_first_cg = replace(first_cg, legs=(new_first_leg,) + first_cg.legs[1:])
        # Build the parking CostGroup
        new_parking_group = CostGroup(
            legs=(JourneyLeg(mode=LegMode.PARK, duration_minutes=0),),
            operator=car_park.name,
            cost=car_park.daily_cost,
        )

        # Attribute the original transit fare to new_first_cg (drive + transit legs)
        # if it has no cost yet.  Always attribute (even if 0) so the frontend
        # shows the cost leg and CommuteSelectorNode can apply the NR fare.
        if new_first_cg.cost is None:
            has_transit = any(
                leg.mode in (LegMode.TRAIN, LegMode.TUBE, LegMode.BUS)
                for leg in new_first_cg.legs
            )
            if has_transit:
                new_first_cg = replace(new_first_cg, cost=existing_cost)

        new_details = (new_first_cg, new_parking_group)
        # Recalculate duration from replaced legs
        new_duration = Quantity(
            sum(leg.duration_minutes for cg in new_details for leg in cg.legs),
            "minute"
        )
        new_commute = replace(
            commute,
            daily_cost=new_cost,
            details=new_details,
            duration=new_duration,
        )

        self._car_park_name = car_park.name or "unknown car park"
        return Attempt.succeeded(new_commute)

    async def build_provenance(self) -> Provenance:
        sources: dict[str, Provenance] = {}
        for dep in self._get_active_deps():
            sources[dep._id] = await dep.build_provenance()
        label = self.display_name
        description = f"parking at {self._car_park_name}" if self._car_park_name else ""
        return Provenance(label=label, description=description, sources=sources)
