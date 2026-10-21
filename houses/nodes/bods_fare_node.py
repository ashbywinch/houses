"""BODS bus-fare lookup for the stops a bus route found."""

from __future__ import annotations

from typing import override

from money import Money

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from houses.bus_fare_reader import get_bus_fare_reader
from houses.bus_journey import cheapest_round_trip
from houses.geopoint import GeoPoint
from serialization import MoneyValue, SerializableAsDict


class _BodsFareValue(SerializableAsDict):
    """Wire shape of the BodsFareNode value persisted to the DAG store:
    the cheapest round-trip fare for each departure stop."""

    stop_fares: dict[str, MoneyValue]


class BodsFareNode(DerivedNode[dict]):
    """Look up BODS bus fares for the stops found by BusRouteNode."""

    def __init__(self, node_id: str, *, bus_route_node):
        super().__init__(node_id, dict, (bus_route_node,))

    @override
    @staticmethod
    def compute(route: Attempt[dict]) -> Attempt[dict]:
        route_val = route.value_or_none()
        if route_val is None:
            return Attempt.impossible("no bus route data")

        bus_stops = route_val.get("bus_stops", [])
        if not bus_stops:
            return Attempt.succeeded(_BodsFareValue(stop_fares={}).to_dict())

        reader = get_bus_fare_reader()
        stop_fares: dict[str, Money] = {}
        for stop in bus_stops:
            dep_name = stop.get("departure_name", "")
            arr_name = stop.get("arrival_name", "")
            dep_point = (
                GeoPoint(stop["departure_lat"], stop["departure_lon"])
                if stop.get("departure_lat") is not None
                else None
            )
            arr_point = (
                GeoPoint(stop["arrival_lat"], stop["arrival_lon"])
                if stop.get("arrival_lat") is not None
                else None
            )
            fares = reader.fares_for_stops(dep_name, arr_name, dep_point=dep_point, arr_point=arr_point)
            cheapest = cheapest_round_trip(fares, reader.national_max_single)
            if cheapest is not None:
                stop_fares[dep_name] = cheapest

        return Attempt.succeeded(_BodsFareValue(stop_fares=stop_fares).to_dict())
