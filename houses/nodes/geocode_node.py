from __future__ import annotations

from typing import override

from dag.attempt import Attempt, SourceType
from dag.derived_node import DerivedNode
from houses.geopoint import GeoPoint
from houses.services_provider import get_services


class GeocodeNode(DerivedNode[GeoPoint]):
# lucidlint: ignore detached-method staticmethod would break instantiation/super()
    def __init__(self, node_id: str, *, best_address):
        super().__init__(node_id, GeoPoint, (best_address,))

    @override
    @staticmethod
    async def compute(address: Attempt[str]) -> Attempt[GeoPoint]:
        addr = address.value_or_none() or ""
        svc = get_services()
        result = await svc.geocoder.geocode_address(addr)
        return result

    provenance_source_type = SourceType.GEOCODE

    # Default build_provenance() walks best_address dep.
