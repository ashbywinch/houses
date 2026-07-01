from __future__ import annotations

import logging

from dag.attempt import Attempt, Provenance
from dag.computed_node import ComputedNode
from houses.geo import GeoPoint

logger = logging.getLogger(__name__)


class WalkabilityNode(ComputedNode[dict]):
    """Async node that computes walkability via the walkability service."""

    def __init__(self, node_id: str, *, best_location, best_address):
        super().__init__(
            node_id,
            dict,
            (best_location, best_address),
        )

    async def compute(self, location: Attempt[GeoPoint],
                      address: Attempt[str]) -> Attempt[dict]:
        from houses.context import get_services

        if not location.is_succeeded:
            return self._impossible({"best_location": location})
        loc = location.value_or_none()
        addr = address.value_or_none() if address.is_succeeded else ""
        result = await get_services().walkability_service.enrich(loc.lat, loc.lon, addr)
        return Attempt.succeeded(
            result,
            Provenance("walkability", description=f"walkability at {loc.lat},{loc.lon}"),
        )


class TownDescNode(ComputedNode[str]):
    """Async node that generates a town description via the town desc service."""

    def __init__(self, node_id: str, *, best_location):
        super().__init__(
            node_id,
            str,
            (best_location,),
        )

    async def compute(self, location: Attempt[GeoPoint]) -> Attempt[str]:
        from houses.context import get_services

        if not location.is_succeeded:
            return self._impossible({"best_location": location})
        loc = location.value_or_none()
        desc = await get_services().town_desc_service.describe("", f"{loc.lat},{loc.lon}")
        return Attempt.succeeded(
            desc,
            Provenance("LLM", description=f"town description for {loc.lat},{loc.lon}"),
        )


class TownNode(ComputedNode[str]):
    """Sync node that extracts the town name from best_address.

    Used by the walk-to-town commute calculation.
    """

    def __init__(self, node_id: str, *, best_address):
        super().__init__(node_id, str, (best_address,))

    async def compute(self, best_address: Attempt[str]) -> Attempt[str]:
        from houses.walkability import _extract_town

        if not best_address.is_succeeded:
            return self._impossible({"best_address": best_address})
        town = _extract_town(best_address.value_or_none() or "")
        if town:
            return Attempt.succeeded(
                town,
                Provenance("address", description=f"town: {town}"),
            )
        addr = best_address.value_or_none() or "?"
        return Attempt.impossible("no town found in address",
                                   Provenance("address", description=f"address: {addr[:50]}"))
