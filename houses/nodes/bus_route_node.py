"""Bus route lookup via Google Routes TRANSIT, with its wire shapes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import override

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from houses.bus_route import BusRoute
from houses.model.domain import PlaceOfInterest
from serialization import SerializableAsDict


class _BusStop(SerializableAsDict):
    """Wire shape of one bus-stop entry in the serialized node value."""

    departure_name: str
    arrival_name: str
    departure_lat: float | None
    departure_lon: float | None
    arrival_lat: float | None
    arrival_lon: float | None


class _BusRouteValue(SerializableAsDict):
    """Wire shape of the BusRouteNode value persisted to the DAG store."""

    bus_stops: list[_BusStop]
    duration_minutes: int


class BusRouteNode(DerivedNode[dict]):
    """Find a bus alternative via Google Routes TRANSIT."""

    _default_google_routes_post: Callable[..., Awaitable[BusRoute | None]] | None = None

    def __init__(self, node_id: str, *, best_location, poi, _google_routes_post=None):
        self._google_routes_post: Callable[..., Awaitable[BusRoute | None]] | None = (
            _google_routes_post or self._default_google_routes_post
        )
        super().__init__(node_id, dict, (best_location, poi))

    @override
    async def compute(self, location: Attempt, dest: Attempt) -> Attempt[dict]:
        loc = location.value_or_none()
        dest_val = dest.value_or_none()
        if not loc or not dest_val:
            return self._impossible(dict(zip(("location", "dest"), (location, dest), strict=True)))

        grp = self._google_routes_post
        if grp is None:
            return Attempt.impossible("Google Routes posting function not configured")

        # The pipeline feeds this node the destination as the household
        # settings define it — a PlaceOfInterest — as well as a bare
        # address or a point, depending on the caller.
        if isinstance(dest_val, PlaceOfInterest):
            dest_str = dest_val.address
        elif isinstance(dest_val, str):
            dest_str = dest_val
        else:
            dest_str = f"{dest_val.lat},{dest_val.lon}"
        if not dest_str:
            return Attempt.impossible("no destination address for this journey")
        origin_str = loc if isinstance(loc, str) else f"{loc.lat},{loc.lon}"

        route = await grp(origin=origin_str, destination=dest_str)
        if route is None or not route.stops:
            # No bus on this journey is a "no bus route" answer, not a
            # failure — the bus augment treats an empty route as pass-through.
            return Attempt.succeeded(_BusRouteValue(bus_stops=[], duration_minutes=0).to_dict())

        bus_stops = [
            _BusStop(
                departure_name=stop.departure_name,
                arrival_name=stop.arrival_name,
                departure_lat=stop.departure_lat,
                departure_lon=stop.departure_lon,
                arrival_lat=stop.arrival_lat,
                arrival_lon=stop.arrival_lon,
            )
            for stop in route.stops
        ]
        return Attempt.succeeded(
            _BusRouteValue(
                bus_stops=bus_stops,
                duration_minutes=round(route.duration.to("minute").magnitude),
            ).to_dict()
        )
