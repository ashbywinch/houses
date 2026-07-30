"""DAG nodes for bus route lookup, fare lookup, and walk-to-bus replacement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from houses.bus_fare_reader import get_bus_fare_reader
from houses.bus_journey import cheapest_round_trip
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.config import settings
from houses.model.domain import Commute
from houses.routing import CommuteRouter


class BusRouteNode(DerivedNode[dict]):
    """Find a bus alternative via Google Routes TRANSIT."""

    _default_google_routes_post: Callable | None = None

    def __init__(self, node_id: str, *, best_location, poi, _google_routes_post=None):
        self._google_routes_post = _google_routes_post or self._default_google_routes_post
        super().__init__(node_id, dict, (best_location, poi))

    async def compute(self, location: Attempt, dest: Attempt) -> Attempt[dict]:
        loc = location.value_or_none()
        dest_val = dest.value_or_none()
        if not loc or not dest_val:
            return self._impossible({"location": location, "dest": dest})

        grp = self._google_routes_post
        if grp is None:
            return Attempt.impossible("Google Routes posting function not configured")

        dest_str = dest_val if isinstance(dest_val, str) else f"{dest_val.lat},{dest_val.lon}"
        origin_str = loc if isinstance(loc, str) else f"{loc.lat},{loc.lon}"

        body = {
            "origin": CommuteRouter._address_waypoint(origin_str),
            "destination": CommuteRouter._address_waypoint(dest_str),
            "travelMode": "TRANSIT",
            "transitPreferences": {"routingPreference": "less_walking"},
            "computeAlternativeRoutes": False,
        }
        data = await grp(body, "routes.duration,routes.legs", timeout=15.0)
        if data is None:
            return Attempt.impossible("Google Routes returned no data")

        routes = data.get("routes", [])
        if not routes:
            return Attempt.impossible("Google Routes returned no routes")

        leg = routes[0].get("legs", [{}])[0]
        steps = leg.get("steps", [])
        bus_stops = []
        for s in steps:
            if s.get("travelMode") != "TRANSIT":
                continue
            td = s.get("transitDetails", {})
            if td.get("transitLine", {}).get("vehicle", {}).get("type") != "BUS":
                continue
            dep = td.get("stopDetails", {}).get("departureStop", {})
            arr = td.get("stopDetails", {}).get("arrivalStop", {})
            bus_stops.append(
                {
                    "departure_name": dep.get("name", ""),
                    "arrival_name": arr.get("name", ""),
                    "departure_lat": dep.get("location", {}).get("latLng", {}).get("latitude"),
                    "departure_lon": dep.get("location", {}).get("latLng", {}).get("longitude"),
                    "arrival_lat": arr.get("location", {}).get("latLng", {}).get("latitude"),
                    "arrival_lon": arr.get("location", {}).get("latLng", {}).get("longitude"),
                }
            )

        if not bus_stops:
            return Attempt.impossible("No bus legs found in route")

        duration_sec = int(routes[0].get("duration", "0s").removesuffix("s"))
        return Attempt.succeeded(
            {
                "bus_stops": bus_stops,
                "duration_minutes": round(duration_sec / 60),
            }
        )


class BodsFareNode(DerivedNode[dict]):
    """Look up BODS bus fares for the stops found by BusRouteNode."""

    def __init__(self, node_id: str, *, bus_route_node):
        super().__init__(node_id, dict, (bus_route_node,))

    def compute(self, route: Attempt[dict]) -> Attempt[dict]:
        route_val = route.value_or_none()
        if route_val is None:
            return Attempt.impossible("no bus route data")

        bus_stops = route_val.get("bus_stops", [])
        if not bus_stops:
            return Attempt.succeeded({"stop_fares": {}})

        reader = get_bus_fare_reader()
        stop_fares = {}
        for stop in bus_stops:
            dep_name = stop.get("departure_name", "")
            arr_name = stop.get("arrival_name", "")
            dep_point = (
                {"lat": stop["departure_lat"], "lon": stop["departure_lon"]}
                if stop.get("departure_lat") is not None
                else None
            )
            arr_point = (
                {"lat": stop["arrival_lat"], "lon": stop["arrival_lon"]}
                if stop.get("arrival_lat") is not None
                else None
            )
            fares = reader.fares_for_stops(dep_name, arr_name, dep_point=dep_point, arr_point=arr_point)
            cheapest = cheapest_round_trip(fares, reader.national_max_single)
            if cheapest is not None:
                stop_fares[dep_name] = {
                    "amount": str(cheapest.amount),
                    "currency": "GBP",
                }

        return Attempt.succeeded({"stop_fares": stop_fares})


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
        max_walk: int = 30,
    ):
        self._transit_input = transit_input
        self._bus_route_node = bus_route_node
        self._bods_fare_node = bods_fare_node
        self._max_walk = max_walk
        super().__init__(node_id, Commute, (transit_input, bus_route_node, bods_fare_node))

    def _get_active_deps(self):
        """Only need bus route and fare when the walk is too long."""
        deps = [self._transit_input]
        transit_attempt = self._transit_input.latest_attempt()
        if transit_attempt.succeeded:
            val = transit_attempt.value_or_none()
            if val is not None and self._walk_too_long(val):
                deps.append(self._bus_route_node)
                deps.append(self._bods_fare_node)
        return tuple(deps)

    def _walk_too_long(self, commute: Commute) -> bool:
        if not commute.details:
            return False
        first_legs = commute.details[0].legs
        if not first_legs:
            return False
        if first_legs[0].mode != LegMode.WALK:
            return False
        return int(first_legs[0].duration.magnitude) > self._max_walk

    def compute(
        self,
        transit_attempt: Attempt[Commute],
        bus_route_attempt: Attempt[dict] | None = None,
        bods_fare_attempt: Attempt[dict] | None = None,
    ) -> Attempt[Commute]:
        if not transit_attempt.succeeded:
            return transit_attempt

        commute = transit_attempt.value_or_none()
        if commute is None:
            return transit_attempt

        if not self._walk_too_long(commute):
            return Attempt.succeeded(commute)

        # Walk is too long — need bus route and fare
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
        total_bus_cost = Money("0", "GBP")
        for stop in bus_stops:
            dep = stop.get("departure_name", "")
            fare_info = stop_fares.get(dep)
            if fare_info:
                total_bus_cost += Money(str(fare_info["amount"]), fare_info["currency"])

        walk_minutes = int(commute.details[0].legs[0].duration.magnitude)
        penalty = int(settings.bus_walk_penalty.magnitude)
        savings = walk_minutes - bus_time
        if savings < penalty:
            return Attempt.succeeded(commute)

        # Build the bus CostGroup
        bus_cg = CostGroup(
            legs=(JourneyLeg(mode=LegMode.BUS, duration=Quantity(bus_time, "minute")),),
            cost=total_bus_cost if total_bus_cost > Money("0", "GBP") else None,
        )

        new_duration = int(commute.duration.magnitude - walk_minutes + bus_time)
        new_daily_cost = commute.daily_cost + total_bus_cost

        new_commute = replace(
            commute,
            duration=Quantity(new_duration, "minute"),
            daily_cost=new_daily_cost,
            _details=(bus_cg,) + commute.details[1:],
        )

        return Attempt.succeeded(new_commute)
