"""Reusable fakes and helpers for tests.

Every fake returns minimal data so tests don't hit real APIs.
"""

from __future__ import annotations

from typing import Any

from money import Money
from pint import Quantity

from dag.attempt import Attempt
from houses.council_tax_info import CouncilTaxInfo
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.school import School
from houses.school_gender import SchoolGender
from houses.services import Services

# ── Individual Fake Services ──────────────────────────────────────────


_DEFAULT_POINT = GeoPoint(51.5, -0.1)


class FakeGeocoder:
    """Returns a fixed GeoPoint for any geocode request."""

    def __init__(self, result: GeoPoint | None = _DEFAULT_POINT):
        self.result = result
        self.postcode_calls: list[str] = []
        self.address_calls: list[str] = []

    @property
    def call_count(self) -> int:
        return len(self.address_calls) + len(self.postcode_calls)

    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]:
        self.postcode_calls.append(postcode)
        return Attempt.succeeded(self.result) if self.result else Attempt.impossible("no result")

    async def geocode_address(self, address: str) -> Attempt[GeoPoint]:
        self.address_calls.append(address)
        return Attempt.succeeded(self.result) if self.result else Attempt.impossible("no result")


_DEFAULT_SIMON = Commute(
    person=Person(name="Simon", has_car=False),
    label="Simon (London)",
    destination=PlaceOfInterest(label="Simon (London)", postcode="SW1V 2QQ"),
    duration=Quantity(30, "minute"),
    daily_cost=Money("10.0", "GBP"),
)
_DEFAULT_LORENA = Commute(
    person=Person(name="Lorena", has_car=False),
    label="Lorena (London)",
    destination=PlaceOfInterest(label="Lorena (London)", postcode="EC3A 7LP"),
    duration=Quantity(45, "minute"),
    daily_cost=Money("12.0", "GBP"),
)
_DEFAULT_PETROL = Commute(
    person=Person(name="Simon", has_car=True),
    label="Bracknell Office (RG12 8YA)",
    destination=PlaceOfInterest(label="Bracknell Office (RG12 8YA)", postcode="RG12 8YA"),
    duration=Quantity(90, "minute"),
    daily_cost=Money("12.50", "GBP"),
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

    async def route(
        self,
        origin: str | GeoPoint,
        destination: str | GeoPoint,
        *,
        has_car: bool,
        max_walk_minutes: int,
    ) -> Attempt[Commute]:
        self.calls.append(("route", str(origin)))
        return Attempt.impossible("mocked route")


class FakeSchoolLookup:
    """School lookup that returns whatever school was passed to constructor.

    ``FakeSchoolLookup()`` returns None (no school found).  Override with
    ``FakeSchoolLookup(school=some_school)`` to return a specific school.
    """

    def __init__(self, school: School | None = None):
        self.school = school
        self.find_calls: list[tuple[str, int, str, tuple]] = []

    async def find_nearest(
        self,
        postcode: str,
        child_age: int,
        address: str = "",
        acceptable: tuple[SchoolGender, ...] = (SchoolGender.MIXED,),
    ) -> School | None:
        self.find_calls.append((postcode, child_age, address, acceptable))
        return self.school

    async def school_commute(self, postcode: str, school: School) -> Commute | None:
        from houses.model.domain import Commute, Person, PlaceOfInterest

        return Commute(
            person=Person(name="George", has_car=False, is_child=True),
            label=school.name,
            destination=PlaceOfInterest(label=school.name, postcode=school.postcode),
            duration=Quantity(20, "minute"),
            daily_cost=Money("0", "GBP"),
        )


class FakeWalkability:
    def __init__(self, walk_to_town_minutes: int = 10, amenities: str = ""):
        self.walk_to_town_minutes = walk_to_town_minutes
        self.amenities = amenities

    async def enrich(self, lat: float, lng: float, address: str) -> dict[str, Any]:
        return {"walk_to_town_minutes": self.walk_to_town_minutes, "amenities": self.amenities}


class FakeTownDesc:
    async def describe(self, town_name: str, postcode: str) -> str:
        return "A nice town."


class FakeEPC:
    def __init__(self, band: str = "C"):
        self.band = band
        self.calls: list[tuple[str, str]] = []

    async def lookup(self, postcode: str, address: str = "") -> str:
        self.calls.append((postcode, address))
        return self.band


class FakeCouncilTax:
    def __init__(self, result: CouncilTaxInfo | None = None):
        self.result = result or CouncilTaxInfo(band="D", yearly_cost=2000)

    async def lookup(self, postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]:
        return Attempt.succeeded(self.result)


class FakeRailFare:
    async def enrich(
        self,
        enabled: set[str] | None,
        postcode: str,
        address: str,
        simon: Commute | None,
        lorena: Commute | None,
    ) -> tuple[Commute | None, Commute | None]:
        return None, None


# ── Composite helper ──────────────────────────────────────────────────


_DEFAULT_SCHOOL = School(
    urn="123",
    name="Test School",
    phase="primary",
    gender=SchoolGender.MIXED,
    type_of_establishment="community school",
    postcode="SW1V 2QQ",
    website="https://example.com",
    ofsted_rating="Good",
    inspection_year="2022",
    coords=GeoPoint(lat=51.5, lon=-0.13),
    statutory_low_age=None,
    statutory_high_age=None,
)


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
        school_lookup=FakeSchoolLookup(school=_DEFAULT_SCHOOL),
        walkability_service=FakeWalkability(walk_to_town_minutes=10, amenities="Shops, cafe"),
        town_desc_service=FakeTownDesc(),
        epc_service=FakeEPC(),
        council_tax_service=FakeCouncilTax(),
        rail_fare_service=FakeRailFare(),
    )
    base.update(overrides)
    return Services(**base)
