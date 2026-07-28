from __future__ import annotations

from dag.attempt import Attempt, Provenance, SourceType
from dag.derived_node import DerivedNode
from houses.geo import GeoPoint
from houses.services_provider import get_services


class GeocodeNode(DerivedNode[GeoPoint]):
    def __init__(self, node_id: str, *, best_address):
        super().__init__(node_id, GeoPoint, (best_address,))

    async def compute(self, address: Attempt[str]) -> Attempt[GeoPoint]:
        addr = address.value_or_none() or ""
        svc = get_services()
        result = await svc.geocoder.geocode_address(addr)
        return result

    @property
    def provenance_source_type(self) -> SourceType:
        return SourceType.GEOCODE

    async def build_provenance(self):
        return Provenance(
            label="geocode",
            url="https://postcodes.io/",
            source_type=SourceType.GEOCODE,
            freshness=self._attempt.created_at,
        )
