"""Service protocols and dependency injection container.

Each protocol defines a boundary that enrichment modules implement.
The ``Services`` dataclass bundles all services with real defaults.

Tests create ``FakeServices`` (or a partial override) to replace
specific services without monkeypatching.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Protocol

from dag.attempt import Attempt
from dag.persistence import latest_node_result
from dag.user_input_node import UserInputNode
from houses.commute import Commute
from houses.geo import GeoPoint
from houses.nodes.settings import make_default_financials, make_default_persons, make_default_thresholds
from houses.council_tax_info import CouncilTaxInfo
from houses.council_tax import lookup_council_tax
from houses.enricher import compute_lorena_commute, compute_petrol_cost, compute_simon_commute

from houses.epc import lookup_epc
from houses.location import _geocode_address, geocode
from houses.model.persistence import load_property_data
from houses.school import School
from houses.school_gender import SchoolGender
from houses.schools import compute_school_commute, find_nearest
from houses.town_desc import generate_town_description
from houses.walkability import enrich_walkability

# ── Protocols ──────────────────────────────────────────────────────────


class GeocodingService(Protocol):
    """Resolve a postcode or address to geographic coordinates."""

    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]: ...

    async def geocode_address(self, address: str) -> Attempt[GeoPoint]: ...


class CommuteRoutingService(Protocol):
    """Simon's commute, Lorena's commute, and Bracknell petrol cost."""

    async def simon_commute(self, postcode: str) -> Attempt[Commute]: ...

    async def lorena_commute(self, postcode: str) -> Attempt[Commute]: ...

    async def petrol_cost(self, postcode: str) -> Attempt[Commute]: ...


class SchoolLookupService(Protocol):
    """Find nearest suitable school and compute its commute."""

    async def find_nearest(
        self,
        postcode: str,
        child_age: int,
        address: str = "",
        requirement: SchoolGender = SchoolGender.BOYS,
    ) -> School | None: ...

    async def school_commute(self, postcode: str, school: School) -> Commute | None: ...


class WalkabilityService(Protocol):
    """Walk time to town centre and nearby amenities."""

    async def enrich(self, lat: float, lng: float, address: str) -> dict[str, Any]: ...


class TownDescService(Protocol):
    """LLM-generated description of a town or area."""

    async def describe(self, town_name: str, postcode: str) -> str: ...


class EPCLookupService(Protocol):
    """Energy Performance Certificate band lookup."""

    async def lookup(self, postcode: str, address: str = "") -> str: ...


class CouncilTaxService(Protocol):
    """Council tax band and yearly cost lookup."""

    async def lookup(self, postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]: ...


class RailFareService(Protocol):
    """National Rail fare fallback for commute costs."""

    async def enrich(
        self,
        enabled: set[str] | None,
        postcode: str,
        address: str,
        simon: Commute | None,
        lorena: Commute | None,
    ) -> tuple[Commute | None, Commute | None]: ...


class PersistenceService(Protocol):
    """Persistence operations for the DAG node store."""

    def load_property_data(self, rid: str) -> Any: ...


def _make_settings_source(node_id: str, value_type: type, default_factory):
    """Create a settings UserInputNode from DB or defaults (eager — not lazy)."""
    node = UserInputNode(node_id, value_type)
    persisted = latest_node_result(node_id)
    if persisted and persisted.get("status") == "succeeded":
        val = node._adapter.validate_python(persisted["value"])
        node._value = val
        node._source_label = persisted.get("source_label", "db")
    else:
        node.push(default_factory(), "config")
    return node


# ── Default implementations (thin wrappers around real modules) ────────


class _DefaultGeocoder:
    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]:
        return await geocode(postcode)

    async def geocode_address(self, address: str) -> Attempt[GeoPoint]:
        return await _geocode_address(address)


class _DefaultCommuteRouter:
    async def simon_commute(self, postcode: str) -> Attempt[Commute]:
        return await compute_simon_commute(postcode)

    async def lorena_commute(self, postcode: str) -> Attempt[Commute]:
        return await compute_lorena_commute(postcode)

    async def petrol_cost(self, postcode: str) -> Attempt[Commute]:
        return await compute_petrol_cost(postcode)


class _DefaultSchoolLookup:
    async def find_nearest(
        self,
        postcode: str,
        child_age: int,
        address: str = "",
        requirement: SchoolGender = SchoolGender.BOYS,
    ) -> School | None:
        sch = await find_nearest(postcode, child_age=child_age, address=address, requirement=requirement)
        return sch

    async def school_commute(self, postcode: str, school: School) -> Commute | None:
        return await compute_school_commute(postcode, school)


class _DefaultWalkability:
    async def enrich(self, lat: float, lng: float, address: str) -> dict[str, Any]:
        return await enrich_walkability(lat, lng, address)


class _DefaultTownDesc:
    async def describe(self, town_name: str, postcode: str) -> str:
        return await generate_town_description(town_name, postcode)


class _DefaultEPCLookup:
    async def lookup(self, postcode: str, address: str = "") -> str:
        return await lookup_epc(postcode, address)


class _DefaultCouncilTax:
    async def lookup(self, postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]:
        return await lookup_council_tax(postcode, address)


class _DefaultRailFare:
    async def enrich(
        self,
        enabled: set[str] | None,
        postcode: str,
        address: str,
        simon: Commute | None,
        lorena: Commute | None,
    ) -> tuple[Commute | None, Commute | None]:
        from houses.enrichment_runner import _enrich_rail_fares

        return await _enrich_rail_fares(enabled, postcode, address, simon, lorena)


class _DefaultPersistence:
    def load_property_data(self, rid: str) -> Any:
        return load_property_data(rid)


# ── DI Container ──────────────────────────────────────────────────────


@dataclasses.dataclass
class Services:
    """All enrichment services with real defaults.

    Usage in production::

        svc = Services()
        result = await svc.commute_router.simon_commute("RG14 1AA")

    Usage in tests::

        svc = Services(commute_router=FakeCommuteRouter(result=...))
    """

    geocoder: GeocodingService = dataclasses.field(default_factory=_DefaultGeocoder)
    commute_router: CommuteRoutingService = dataclasses.field(default_factory=_DefaultCommuteRouter)
    school_lookup: SchoolLookupService = dataclasses.field(default_factory=_DefaultSchoolLookup)
    walkability_service: WalkabilityService = dataclasses.field(default_factory=_DefaultWalkability)
    town_desc_service: TownDescService = dataclasses.field(default_factory=_DefaultTownDesc)
    epc_service: EPCLookupService = dataclasses.field(default_factory=_DefaultEPCLookup)
    council_tax_service: CouncilTaxService = dataclasses.field(default_factory=_DefaultCouncilTax)
    rail_fare_service: RailFareService = dataclasses.field(default_factory=_DefaultRailFare)
    persistence: PersistenceService = dataclasses.field(default_factory=_DefaultPersistence)

    # ── Settings sources (eager singletons, not module-level) ──────────
    persons_source: UserInputNode[list] = dataclasses.field(
        default_factory=lambda: _make_settings_source("persons", list, make_default_persons))
    financial_source: UserInputNode[dict] = dataclasses.field(
        default_factory=lambda: _make_settings_source("financial", dict, make_default_financials))
    commute_thresholds_source: UserInputNode[dict] = dataclasses.field(
        default_factory=lambda: _make_settings_source("commute_thresholds", dict, make_default_thresholds))
