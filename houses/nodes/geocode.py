from __future__ import annotations

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode
from houses.services_provider import get_services
from houses.geo import GeoPoint


class GeocodeNode(DerivedNode[GeoPoint]):
    def __init__(self, node_id: str, *, best_address):
        super().__init__(node_id, GeoPoint, (best_address,))

    async def compute(self, address: Attempt[str]) -> Attempt[GeoPoint]:
        if not address.succeeded:
            return self._impossible({"best_address": address})
        addr = address.value_or_none() or ""
        svc = get_services()
        result = await svc.geocoder.geocode_address(addr)
        return result

    async def build_provenance(self):
        return Provenance(label="geocode")
