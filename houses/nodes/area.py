from __future__ import annotations

from dag.attempt import Attempt, Provenance
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

    def _is_transient_error(self, exc: Exception) -> bool:
        from houses.helpers import is_transient_error as _ite
        return _ite(exc)

    async def build_provenance(self):
        return Provenance(label="walkability")


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
        town = await svc.geocoder.reverse_geocode_town(loc.lat, loc.lon)
        if town:
            return Attempt.succeeded(town)
        return Attempt.impossible("could not determine nearest town")

    def _is_transient_error(self, exc: Exception) -> bool:
        from houses.helpers import is_transient_error as _ite
        return _ite(exc)

    async def build_provenance(self):
        return Provenance(label="reverse_geocode")


class TownDescNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_location, nearest_town, town_name, postcode_node):
        deps: tuple[Node, ...] = (best_location, nearest_town, town_name, postcode_node)
        super().__init__(node_id, dict, deps)

    @property
    def _skip_impossible_dep_check(self) -> bool:
        return True

    async def compute(self, location: Attempt[GeoPoint], nearest_town: Attempt[str], town_name: Attempt[str], postcode: Attempt[str]) -> Attempt[dict]:
        # Prefer the address-extracted town name (more specific), fall back to
        # reverse-geocoded town when the address has no recognizable town.
        town = town_name.value_or_none() or nearest_town.value_or_none()
        pc = postcode.value_or_none() or ""
        if not town:
            return Attempt.impossible("no town name available from address or reverse geocode")
        svc = get_services()
        desc = await svc.town_desc_service.describe(town, pc)
        return Attempt.succeeded({"description": desc})

    def _is_transient_error(self, exc: Exception) -> bool:
        from houses.helpers import is_transient_error as _ite
        return _ite(exc)

    async def build_provenance(self):
        return Provenance(label="LLM")


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

    async def build_provenance(self):
        return Provenance(label="address")
