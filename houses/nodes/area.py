from __future__ import annotations

from typing import override

from dag.attempt import Attempt, Provenance, SourceType
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.geo import GeoPoint
from houses.services_provider import get_services
from houses.walkability import _extract_town


class WalkabilityNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_location, best_address):
        deps: tuple[Node, ...] = (best_location, best_address)
        super().__init__(node_id, dict, deps)

    async def compute(self, location: Attempt[GeoPoint], address: Attempt[str]) -> Attempt[dict]:
        loc = location.value_or_none()
        svc = get_services()
        result = await svc.walkability_service.enrich(loc.lat, loc.lon, address.value_or_none() or "")
        return Attempt.succeeded(result)

    @override
    async def build_provenance(self) -> Provenance:
        prov = await super().build_provenance()
        val = self._attempt.value_or_none()
        if self._attempt.succeeded and isinstance(val, dict):
            parts: list[str] = []
            wt = val.get("walk_to_town")
            if isinstance(wt, dict) and wt.get("value") is not None:
                parts.append(f"{wt['value']} min walk to town")
            if val.get("amenities"):
                parts.append(val["amenities"])
            if parts:
                prov.value = " · ".join(parts)
        return prov

    @property
    def provenance_source_type(self) -> SourceType:
        return SourceType.API


class NearestTownNode(DerivedNode[str]):
    """Reverse-geocode the property's location to find the nearest town name."""

    def __init__(self, node_id: str, *, best_location):
        deps: tuple[Node, ...] = (best_location,)
        super().__init__(node_id, str, deps)

    async def compute(self, location: Attempt[GeoPoint]) -> Attempt[str]:
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

    @property
    def provenance_source_type(self) -> SourceType:
        return SourceType.GEOCODE


class TownDescNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_location, nearest_town, town_name, postcode_node):
        self._postcode_node = postcode_node
        self.best_location = best_location
        self._nearest_town = nearest_town
        self._town_name = town_name
        deps: tuple[Node, ...] = (best_location, nearest_town, town_name, postcode_node)
        super().__init__(node_id, dict, deps)

    def _get_active_deps(self):
        """The postcode is an optional refinement for the LLM prompt — a
        pending/empty postcode (a property with no known postcode) must
        not stall the town description.  The describe call works with an
        empty postcode string."""
        deps: list[Node] = [self.best_location, self._nearest_town, self._town_name]
        pc = self._postcode_node.latest_attempt()
        if pc.succeeded and pc.value_or_none():
            deps.append(self._postcode_node)
        return tuple(deps)

    async def compute(
        self,
        location: Attempt[GeoPoint],
        nearest_town: Attempt[str],
        town_name: Attempt[str],
        postcode: Attempt[str] | None = None,
    ) -> Attempt[dict]:
        # Prefer the address-extracted town name (more specific), fall back to
        # reverse-geocoded town when the address has no recognizable town.
        town = town_name.value_or_none() or nearest_town.value_or_none()
        pc = postcode.value_or_none() if postcode is not None else None
        pc = pc or ""
        if not town:
            return Attempt.impossible("no town name available from address or reverse geocode")
        svc = get_services()
        result = await svc.town_desc_service.describe(town, pc)
        if result.impossible:
            # Propagate the real reason (e.g. LLM call failed) so the
            # frontend can show it.
            return Attempt.impossible(result.error or "town description unavailable")
        return Attempt.succeeded({"description": result.value_or_none() or ""})

    @override
    async def build_provenance(self) -> Provenance:
        prov = await super().build_provenance()
        val = self._attempt.value_or_none()
        if self._attempt.succeeded and isinstance(val, dict) and val.get("description"):
            prov.value = val["description"]
        return prov

    @property
    def provenance_source_type(self) -> SourceType:
        return SourceType.API


class TownNode(DerivedNode[str]):
    def __init__(self, node_id: str, *, best_address):
        deps: tuple[Node, ...] = (best_address,)
        super().__init__(node_id, str, deps)

    def compute(self, address: Attempt[str]) -> Attempt[str]:
        addr = address.value_or_none() or ""
        town = _extract_town(addr)
        if town:
            return Attempt.succeeded(town)
        return Attempt.impossible("no town found in address")

    @property
    def provenance_source_type(self) -> SourceType:
        return SourceType.CALC

    # Default build_provenance() walks active deps and uses provenance_source_type.
