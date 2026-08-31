"""DAG node that looks up the nearest railway/Tube station to a GeoPoint."""

from __future__ import annotations

from typing import override

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.geopoint import GeoPoint
from houses.rail_fare_registry import get_rail_fare_registry
from houses.stations import Station


class NearestStationNode(DerivedNode[Station | None]):
    """Derived node that finds the nearest station to a property location.

    Depends on a single parent node providing the property's ``GeoPoint``
    and delegates to ``RailFareRegistry.nearest_station`` for the lookup.
    """

    def __init__(self, node_id: str, *, best_location: Node[GeoPoint]) -> None:
        super().__init__(node_id, Station | None, (best_location,))

    @override
    @staticmethod
    def compute(location: Attempt[GeoPoint]) -> Attempt[Station | None]:
        loc = location.value_or_none()
        if loc is None:
            return Attempt.succeeded(None)
        registry = get_rail_fare_registry()
        station = registry.nearest_station(loc)
        if station is None:
            return Attempt.succeeded(None)
        return Attempt.succeeded(station)
