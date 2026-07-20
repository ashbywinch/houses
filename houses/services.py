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
from houses.council_tax import lookup_council_tax
from houses.council_tax_info import CouncilTaxInfo
from houses.epc import lookup_epc
from houses.geo import GeoPoint
from houses.location import _geocode_address, geocode
from houses.model.domain import Commute, Person
from houses.nodes.settings import make_default_financials, make_default_persons, make_default_thresholds
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
    """Generic routing from an origin to a destination."""

    async def route(
        self,
        origin: str | GeoPoint,
        destination: str | GeoPoint,
        *,
        has_car: bool,
        max_walk_minutes: int,
    ) -> Attempt[Commute]: ...


class SchoolLookupService(Protocol):
    """Find nearest suitable school and compute its commute."""

    async def find_nearest(
        self,
        postcode: str,
        child_age: int,
        address: str = "",
        acceptable: tuple[SchoolGender, ...] = (SchoolGender.MIXED,),
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


class DriveTimeService(Protocol):
    """Estimate driving time from an origin postcode to a station."""

    async def estimate(self, origin_postcode: str, station_name: str) -> int | None: ...


class _DefaultDriveTimeService:
    async def estimate(self, origin_postcode: str, station_name: str) -> int | None:
        from houses.transit_route import _get_drive_minutes

        return await _get_drive_minutes(origin_postcode, station_name)




# Settings sources are cached by node_id so that the same UserInputNode
# instance is returned on every Services() construction.  This means
# a PATCH to /api/settings/financial updates the canonical node that
# all PropertyNodes reference, without needing a server restart.
_SETTINGS_SOURCE_CACHE: dict[str, UserInputNode] = {}
def _reset_settings_cache():
    """Clear the settings source cache for test isolation."""
    _SETTINGS_SOURCE_CACHE.clear()


def _make_settings_source(node_id: str, value_type: type, default_factory):
    if node_id in _SETTINGS_SOURCE_CACHE:
        return _SETTINGS_SOURCE_CACHE[node_id]
    node = UserInputNode(node_id, value_type)
    persisted = latest_node_result(node_id)
    if persisted and persisted.get("status") == "succeeded":
        source_label = persisted.get("source_label", "db")
        if source_label == "tests":
            raise RuntimeError(
                f"Stale test data (source_label='tests') found in production DB "
                f"for settings node '{node_id}'. "
                f"Test data leaked from a test run that bypassed DB isolation. "
                f"Remove the offending row from node_results to recover."
            )
        val = node._adapter.validate_python(persisted["value"])
        node._value = val
        node._source_label = source_label
    else:
        node.push(default_factory(), "config")
    _SETTINGS_SOURCE_CACHE[node_id] = node
    return node


# ── Default implementations (thin wrappers around real modules) ────────


class _DefaultGeocoder:
    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]:
        return await geocode(postcode)

    async def geocode_address(self, address: str) -> Attempt[GeoPoint]:
        return await _geocode_address(address)


class _DefaultCommuteRouter:
    async def route(
        self,
        origin: str | GeoPoint,
        destination: str | GeoPoint,
        *,
        has_car: bool,
        max_walk_minutes: int,
    ) -> Attempt[Commute]:
        from houses.routing import get_commute

        return await get_commute(origin, destination, has_car=has_car, max_walk_minutes=max_walk_minutes)


class _DefaultSchoolLookup:
    async def find_nearest(
        self,
        postcode: str,
        child_age: int,
        address: str = "",
        acceptable: tuple[SchoolGender, ...] = (SchoolGender.MIXED,),
    ) -> School | None:
        return await find_nearest(postcode, child_age=child_age, address=address, acceptable=acceptable)

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
        # NR fare enrichment is now handled by RailFareNode in the DAG pipeline
        return simon, lorena



# ── DI Container ──────────────────────────────────────────────────────


@dataclasses.dataclass
class Services:
    geocoder: GeocodingService = dataclasses.field(default_factory=_DefaultGeocoder)
    commute_router: CommuteRoutingService = dataclasses.field(default_factory=_DefaultCommuteRouter)
    school_lookup: SchoolLookupService = dataclasses.field(default_factory=_DefaultSchoolLookup)
    walkability_service: WalkabilityService = dataclasses.field(default_factory=_DefaultWalkability)
    town_desc_service: TownDescService = dataclasses.field(default_factory=_DefaultTownDesc)
    epc_service: EPCLookupService = dataclasses.field(default_factory=_DefaultEPCLookup)
    council_tax_service: CouncilTaxService = dataclasses.field(default_factory=_DefaultCouncilTax)
    rail_fare_service: RailFareService = dataclasses.field(default_factory=_DefaultRailFare)
    drive_time_service: DriveTimeService = dataclasses.field(default_factory=_DefaultDriveTimeService)
    persons_source: UserInputNode[list[Person]] = dataclasses.field(
        default_factory=lambda: _make_settings_source("persons", list[Person], make_default_persons)
    )
    financial_source: UserInputNode[dict] = dataclasses.field(
        default_factory=lambda: _make_settings_source("financial", dict, make_default_financials)
    )
    commute_thresholds_source: UserInputNode[dict] = dataclasses.field(
        default_factory=lambda: _make_settings_source("commute_thresholds", dict, make_default_thresholds)
    )
    # Per-request mutable state (lazily initialized by accessors)
    geo_state: Any | None = None
    geo_cache: dict | None = None
    bus_fare_registry: Any | None = None
    rail_fare_registry: Any | None = None
