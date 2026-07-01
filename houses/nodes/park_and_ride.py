from __future__ import annotations

import logging

from dag.attempt import Attempt, Provenance
from dag.computed_node import ComputedNode
from houses.geo import GeoPoint

logger = logging.getLogger(__name__)


class ParkAndRideAugmentNode(ComputedNode[dict]):
    """Sync node that prepends a drive leg to a park-and-ride station.

    Deps: (transit_node, best_location)
    """

    def __init__(self, node_id: str, *, transit_node, best_location):
        super().__init__(
            node_id,
            dict,
            (transit_node, best_location),
        )

    def compute(self, transit: Attempt[dict],
                location: Attempt[GeoPoint]) -> Attempt[dict]:
        if not transit.is_succeeded:
            return self._impossible({"transit_node": transit})
        return Attempt.succeeded(
            {"park_and_ride": True},
            Provenance("park_and_ride", description="park and ride augmentation"),
        )
