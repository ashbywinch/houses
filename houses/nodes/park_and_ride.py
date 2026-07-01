from __future__ import annotations

from dag.attempt import Attempt
from dag.computed_node import ComputedNode


class ParkAndRideAugmentNode(ComputedNode[dict]):
    def __init__(self, node_id: str, *, transit_node):
        super().__init__(node_id, dict, (transit_node,))

    def compute(self, transit: Attempt[dict]) -> Attempt[dict]:
        if not transit.succeeded:
            return self._impossible({"transit_node": transit})
        return Attempt.succeeded({"park_and_ride": True})

    async def build_provenance(self):
        from dag.attempt import Provenance
        return Provenance(label="park_and_ride")
