from __future__ import annotations

from dag.attempt import Attempt
from dag.computed_node import ComputedNode
from dag.node import Node
from houses.geo import GeoPoint


class WalkabilityNode(ComputedNode[dict]):
    def __init__(self, node_id: str, *, best_location, best_address):
        deps: tuple[Node, ...] = (best_location, best_address)
        super().__init__(node_id, dict, deps)

    async def compute(self, location: Attempt[GeoPoint],
                      address: Attempt[str]) -> Attempt[dict]:
        from houses.context import get_services

        if not location.succeeded:
            return self._impossible({"best_location": location})
        loc = location.value_or_none()
        svc = get_services()
        result = await svc.walkability_service.enrich(loc.lat, loc.lon, address.value_or_none() or "")
        return Attempt.succeeded(result)

    async def build_provenance(self):
        from dag.attempt import Provenance
        return Provenance(label="walkability")


class TownDescNode(ComputedNode[dict]):
    def __init__(self, node_id: str, *, best_location):
        super().__init__(node_id, dict, (best_location,))

    async def compute(self, location: Attempt[GeoPoint]) -> Attempt[dict]:
        from houses.context import get_services

        if not location.succeeded:
            return self._impossible({"best_location": location})
        loc = location.value_or_none()
        svc = get_services()
        desc = await svc.town_desc_service.describe("", f"{loc.lat},{loc.lon}")
        return Attempt.succeeded({"description": desc})

    async def build_provenance(self):
        from dag.attempt import Provenance
        return Provenance(label="LLM")


class TownNode(ComputedNode[str]):
    def __init__(self, node_id: str, *, best_address):
        deps: tuple[Node, ...] = (best_address,)
        super().__init__(node_id, str, deps)

    def compute(self, address: Attempt[str]) -> Attempt[str]:
        if not address.succeeded:
            return self._impossible({"best_address": address})
        from houses.walkability import _extract_town
        addr = address.value_or_none() or ""
        town = _extract_town(addr)
        if town:
            return Attempt.succeeded(town)
        return Attempt.impossible("no town found in address")

    async def build_provenance(self):
        from dag.attempt import Provenance
        return Provenance(label="address")
