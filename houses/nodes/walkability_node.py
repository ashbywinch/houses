"""Walkability summary for a property — the walk-to-town and amenity payload."""

from __future__ import annotations

from typing import override

from dag.attempt import Attempt, Provenance, SourceType
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.geopoint import GeoPoint
from houses.services_provider import get_services


class WalkabilityNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_location, best_address):
        deps: tuple[Node, ...] = (best_location, best_address)
        super().__init__(node_id, dict, deps)

    @override
    @staticmethod
    async def compute(location: Attempt[GeoPoint], address: Attempt[str]) -> Attempt[dict]:
        loc = location.value_or_none()
        if loc is None:
            return Attempt.impossible("location missing")
        svc = get_services()
        payload = await svc.walkability_service.enrich(loc.lat, loc.lon, address.value_or_none() or "")
        return Attempt.succeeded(payload.to_dict())

    @override
    async def build_provenance(
        self, dep_attempts: list[Attempt] | None = None, active_deps: tuple[Node, ...] | None = None
    ) -> Provenance:
        prov = await super().build_provenance(dep_attempts=dep_attempts, active_deps=active_deps)
        val = self._attempt.value_or_none()
        if self._attempt.succeeded and isinstance(val, dict):
            parts: list[str] = []
            wt = val.get("walk_to_town")
            if isinstance(wt, dict) and wt.get("value") is not None:
                parts.append(f"{wt['value']} min walk to town")
            amenities = val.get("amenities")
            # Type-guarded: a non-string amenities must never crash the
            # join, nor leak a container repr into the value.
            if isinstance(amenities, str) and amenities:
                parts.append(amenities)
            prov.value = " · ".join(parts) or "Walkability summary unavailable"
        return prov

    provenance_source_type = SourceType.API
