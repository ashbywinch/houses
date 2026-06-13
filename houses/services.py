"""Service protocols and dependency injection container.

Each protocol defines a boundary that enrichment modules implement.
The ``Services`` dataclass bundles all services with real defaults.

Tests create ``FakeServices`` (or a partial override) to replace
specific services without monkeypatching.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Protocol

from houses.attempt import Attempt
from houses.commute import Commute
from houses.geo import GeoPoint
from houses.property import CouncilTaxInfo
from houses.schools import School, SchoolGender

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


# ── Default implementations (thin wrappers around real modules) ────────


class _DefaultGeocoder:
    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]:
        from houses.location import geocode

        return await geocode(postcode)

    async def geocode_address(self, address: str) -> Attempt[GeoPoint]:
        from houses.location import _geocode_address

        return await _geocode_address(address)


class _DefaultCommuteRouter:
    async def simon_commute(self, postcode: str) -> Attempt[Commute]:
        from houses.enricher import compute_simon_commute

        return await compute_simon_commute(postcode)

    async def lorena_commute(self, postcode: str) -> Attempt[Commute]:
        from houses.enricher import compute_lorena_commute

        return await compute_lorena_commute(postcode)

    async def petrol_cost(self, postcode: str) -> Attempt[Commute]:
        from houses.enricher import compute_petrol_cost

        return await compute_petrol_cost(postcode)


class _DefaultSchoolLookup:
    async def find_nearest(
        self,
        postcode: str,
        child_age: int,
        address: str = "",
        requirement: SchoolGender = SchoolGender.BOYS,
    ) -> School | None:
        from houses.schools import find_nearest

        sch = await find_nearest(postcode, child_age=child_age, address=address, requirement=requirement)
        return sch

    async def school_commute(self, postcode: str, school: School) -> Commute | None:
        from houses.schools import compute_school_commute

        return await compute_school_commute(postcode, school)


class _DefaultWalkability:
    async def enrich(self, lat: float, lng: float, address: str) -> dict[str, Any]:
        from houses.walkability import enrich_walkability

        return await enrich_walkability(lat, lng, address)


class _DefaultTownDesc:
    async def describe(self, town_name: str, postcode: str) -> str:
        from houses.town_desc import generate_town_description

        return await generate_town_description(town_name, postcode)


class _DefaultEPCLookup:
    async def lookup(self, postcode: str, address: str = "") -> str:
        from houses.epc import lookup_epc

        return await lookup_epc(postcode, address)


class _DefaultCouncilTax:
    async def lookup(self, postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]:
        from houses.council_tax import lookup_council_tax

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
