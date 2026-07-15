"""DAG node that looks up the nearest railway/Tube station to a GeoPoint."""

from __future__ import annotations

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.geo import GeoPoint
from houses.stations import Station


class NearestStationNode(DerivedNode[Station | None]):
    """Derived node that finds the nearest station to a property location.

    Depends on a single parent node providing the property's ``GeoPoint``
    and delegates to ``RailFareRegistry.nearest_station`` for the lookup.
    """

    def __init__(self, node_id: str, *, best_location: Node[GeoPoint]) -> None:
        super().__init__(node_id, Station | None, (best_location,))

    def compute(self, location: Attempt[GeoPoint]) -> Attempt[Station | None]:
        if not location.succeeded:
            return self._impossible({"best_location": location})
        from houses.rail_fare_registry import get_rail_fare_registry

        registry = get_rail_fare_registry()
        station = registry.nearest_station(location.value_or_none())
        if station is None:
            return Attempt.succeeded(None)
        return Attempt.succeeded(station)
