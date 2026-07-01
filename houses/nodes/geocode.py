from __future__ import annotations

import logging

from dag.attempt import Attempt, Provenance
from dag.computed_node import ComputedNode
from houses.geo import GeoPoint

logger = logging.getLogger(__name__)


class GeocodeNode(ComputedNode[GeoPoint]):
    """Async node that geocodes the best address via the geocoding service.

    Deps: (best_address)
    """

    def __init__(self, node_id: str, *, best_address):
        super().__init__(
            node_id,
            GeoPoint,
            (best_address,),
        )

    async def compute(self, best_address: Attempt[str]) -> Attempt[GeoPoint]:
        from houses.context import get_services

        if not best_address.is_succeeded:
            return self._impossible({"best_address": best_address})
        address = best_address.value_or_none()
        result = await get_services().geocoder.geocode_address(address)
        if result.is_succeeded:
            gp = result.value_or_none()
            return Attempt.succeeded(
                gp,
                Provenance("geocode", description=f"geocoded: {address}"),
            )
        return self._impossible({"geocode_address": result})
