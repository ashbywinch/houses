"""Reusable fake services for tests using the Services DI container.

Usage::

    from tests.helpers import FakeEPC, FakeCommuteRouter, make_services
    from houses.services import Services

    services = make_services(epc_service=FakeEPC(band="C"))
    result = await _run_enrichment(..., services=services)
"""

from __future__ import annotations

from typing import Any

from houses.attempt import Attempt
from houses.commute import Commute
from houses.geo import GeoPoint
from houses.property import CouncilTaxInfo
from houses.schools import School, SchoolGender
from houses.services import Services

# ── Individual Fake Services ──────────────────────────────────────────


_DEFAULT_POINT = GeoPoint(51.5, -0.1)


class FakeGeocoder:
    """Returns a fixed GeoPoint for any geocode request."""

    def __init__(self, result: GeoPoint | None = _DEFAULT_POINT):
        self.result = result
        self.postcode_calls: list[str] = []
        self.address_calls: list[str] = []

    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]:
        self.postcode_calls.append(postcode)
        return Attempt.succeeded(self.result, "fake") if self.result else Attempt.impossible("fake", "no result")

    async def geocode_address(self, address: str) -> Attempt[GeoPoint]:
        self.address_calls.append(address)
        return Attempt.succeeded(self.result, "fake") if self.result else Attempt.impossible("fake", "no result")


_DEFAULT_SIMON = Commute(
    destination_label="Simon (London)",
    destination_postcode="SW1V 2QQ",
    duration_minutes=30,
    daily_cost_gbp=10.0,
)
_DEFAULT_LORENA = Commute(
    destination_label="Lorena (London)",
    destination_postcode="EC3A 7LP",
    duration_minutes=45,
    daily_cost_gbp=12.0,
)
_DEFAULT_PETROL = Commute(
    destination_label="Bracknell Office (RG12 8YA)",
    destination_postcode="RG12 8YA",
    duration_minutes=90,
    daily_cost_gbp=12.50,
)


class FakeCommuteRouter:
    """Returns canned commute results. Records calls for assertion."""

    def __init__(
        self,
        simon: Commute | None = _DEFAULT_SIMON,
        lorena: Commute | None = _DEFAULT_LORENA,
        petrol: Commute | None = _DEFAULT_PETROL,
    ):
        self.simon = simon
        self.lorena = lorena
        self.petrol = petrol
        self.calls: list[tuple[str, str]] = []

    async def simon_commute(self, postcode: str) -> Attempt[Commute]:
        self.calls.append(("simon", postcode))
        return Attempt.succeeded(self.simon, "fake") if self.simon else Attempt.impossible("fake", "no route")

    async def lorena_commute(self, postcode: str) -> Attempt[Commute]:
        self.calls.append(("lorena", postcode))
        return Attempt.succeeded(self.lorena, "fake") if self.lorena else Attempt.impossible("fake", "no route")

    async def petrol_cost(self, postcode: str) -> Attempt[Commute]:
        self.calls.append(("petrol", postcode))
        return Attempt.succeeded(self.petrol, "fake") if self.petrol else Attempt.impossible("fake", "no route")


class FakeSchoolLookup:
    """Returns no school (None) for any lookup."""

    def __init__(self):
        self.find_calls: list[dict] = []
        self.commute_calls: list[tuple[str, School | None]] = []

    async def find_nearest(
        self,
        postcode: str,
        child_age: int,
        address: str = "",
        requirement: SchoolGender = SchoolGender.BOYS,
    ) -> School | None:
        self.find_calls.append(dict(postcode=postcode, child_age=child_age, address=address, requirement=requirement))
        return None

    async def school_commute(self, postcode: str, school: School) -> Commute | None:
        self.commute_calls.append((postcode, school))
        return None


class FakeWalkability:
    """Returns empty walkability data."""

    def __init__(self, walk_to_town_minutes: int | None = None, amenities: str = ""):
        self._walk_to_town_minutes = walk_to_town_minutes
        self._amenities = amenities

    async def enrich(self, lat: float, lng: float, address: str) -> dict[str, Any]:
        return {"walk_to_town_minutes": self._walk_to_town_minutes, "amenities": self._amenities}


class FakeTownDesc:
    """Returns a canned town description."""

    def __init__(self, description: str = "A pleasant town."):
        self._description = description
        self.calls: list[tuple[str, str]] = []

    async def describe(self, town_name: str, postcode: str) -> str:
        self.calls.append((town_name, postcode))
        return self._description


class FakeEPC:
    """Returns a canned EPC band. Records calls for assertion."""

    def __init__(self, band: str = "C"):
        self.band = band
        self.calls: list[tuple[str, str]] = []

    async def lookup(self, postcode: str, address: str = "") -> str:
        self.calls.append((postcode, address))
        return self.band


class FakeCouncilTax:
    """Returns a canned council tax result."""

    def __init__(self, band: str = "D", cost: float | None = 1800.0):
        if band:
            self._result: Attempt[CouncilTaxInfo] = Attempt.succeeded(
                CouncilTaxInfo(band=band, yearly_cost=cost),
                "fake",
            )
        else:
            self._result = Attempt.impossible("fake", "no result")

    async def lookup(self, postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]:
        return self._result


class FakeRailFare:
    """Passes simon/lorena through unchanged (no rail fare enrichment)."""

    async def enrich(
        self,
        enabled: set[str] | None,
        postcode: str,
        address: str,
        simon: Commute | None,
        lorena: Commute | None,
    ) -> tuple[Commute | None, Commute | None]:
        return simon, lorena


# ── Convenience factory ────────────────────────────────────────────────


def make_services(**overrides: Any) -> Services:
    """Build a ``Services`` with all fakes, optionally overriding specific services.

    Default fakes return minimal data — override any service with a custom fake::

        services = make_services(
            epc_service=FakeEPC(band="B"),
            commute_router=FakeCommuteRouter(simon=None),
        )
    """
    base: dict[str, Any] = dict(
        geocoder=FakeGeocoder(),
        commute_router=FakeCommuteRouter(),
        school_lookup=FakeSchoolLookup(),
        walkability_service=FakeWalkability(walk_to_town_minutes=10, amenities="Shops, cafe"),
        town_desc_service=FakeTownDesc(),
        epc_service=FakeEPC(),
        council_tax_service=FakeCouncilTax(),
        rail_fare_service=FakeRailFare(),
    )
    base.update(overrides)
    return Services(**base)
