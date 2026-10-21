"""Reverse-geocode a property's location to the nearest town name."""

from __future__ import annotations

from typing import override

from dag.attempt import Attempt, SourceType
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.geopoint import GeoPoint
from houses.services_provider import get_services


class NearestTownNode(DerivedNode[str]):
    """Reverse-geocode the property's location to find the nearest town name."""

    def __init__(self, node_id: str, *, best_location):
        deps: tuple[Node, ...] = (best_location,)
        super().__init__(node_id, str, deps)

    @override
    @staticmethod
    async def compute(location: Attempt[GeoPoint]) -> Attempt[str]:
        loc = location.value_or_none()
        if loc is None:
            return Attempt.impossible("no location")
        svc = get_services()
        result = await svc.geocoder.reverse_geocode_town(loc.lat, loc.lon)
        if result.succeeded:
            town = result.value_or_none()
            if town:
                return Attempt.succeeded(town)
        # Propagate the real reason (e.g. "no town found for coordinates")
        # so the frontend can show it.
        return Attempt.impossible(result.error or "could not determine nearest town")

    provenance_source_type = SourceType.GEOCODE
