"""Wiring record for ``ParkAndRideAugmentNode``: the commute chain inputs plus
the station/car-park registries (injected for tests).
"""

from __future__ import annotations

from dataclasses import dataclass

from dag.node import Node
from houses.car_park import CarParkRegistry
from houses.stations import StationRegistry


@dataclass(frozen=True)
class ParkAndRideOptions:
    """Wiring for ``ParkAndRideAugmentNode``: the commute chain inputs plus
    the station/car-park registries (injected for tests)."""

    transit_node: Node
    best_location: Node
    postcode_node: Node
    has_car: bool
    max_walk_node: Node
    station_registry: StationRegistry | None = None
    car_park_registry: CarParkRegistry | None = None
