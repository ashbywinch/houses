"""Reusable fakes and helpers for tests.

Every fake returns minimal data so tests don't hit real APIs.
"""

from __future__ import annotations

import contextlib
from typing import Any, override

from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.measurement import Measurement
from houses.council_tax_info import CouncilTaxInfo
from houses.geopoint import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.school import School
from houses.school_gender import SchoolGender
from houses.services import (
    CouncilTaxService,
    DriveTimeService,
    EPCLookupService,
    GeocodingService,
    OAuthService,
    RailFareService,
    RoutePlanner,
    SchoolLookupService,
    Services,
    TownDescService,
    WalkabilityService,
)


@contextlib.contextmanager
def inject_server_deps(*, scrape_fn=None):
    """Temporarily inject per-request server dependencies.

    ``scrape_fn`` replaces the Rightmove scraper (the ``houses.context``
    seam) — no monkeypatching of module globals.
    """
    from houses import context as _ctx

    saved = []
    if scrape_fn is not None:
        saved.append((_ctx._request_scrape_fn, _ctx._request_scrape_fn.set(scrape_fn)))
    try:
        yield
    finally:
        for var, token in reversed(saved):
            var.reset(token)

# ── Individual Fake Services ──────────────────────────────────────────


class FixedCommuteNode(DerivedNode[Commute]):
    """A DerivedNode that holds a canned Commute value for tests.

    Matches the production architecture: Commutes always come from
    DerivedNodes, never from UserInputNodes.  Call ``set(commute)``
    to update the value and notify downstream nodes.
    """

    def __init__(self, node_id: str):
        super().__init__(node_id, Commute, ())
        self._commute: Commute | None = None

    @override
    def compute(self) -> Attempt[Commute]:
        if self._commute is not None:
            return Attempt.succeeded(self._commute)
        return Attempt.pending()

    def set(self, commute: Commute) -> None:
        self._commute = commute
        self._attempt = Attempt.pending()
        from dag.scheduler import get_scheduler

        get_scheduler().schedule(self)
        self.changed.emit()

    def push(self, value: Commute, source_label: str = "") -> None:
        """API-compatible alias for set(). Accepts a source_label for compatibility."""
        self.set(value)


_DEFAULT_POINT = GeoPoint(51.5, -0.1)


class FakeGeocoder(GeocodingService):
    """Returns a fixed GeoPoint for any geocode request, and a fixed
    town name for reverse-geocode town lookups."""

    def __init__(self, result: GeoPoint | None = _DEFAULT_POINT, reverse_town: str | None = "Test Town"):
        self.result = result
        self.reverse_town = reverse_town
        self.postcode_calls: list[str] = []
        self.address_calls: list[str] = []
        self.reverse_calls: list[tuple[float, float]] = []

    @property
    def call_count(self) -> int:
        return len(self.address_calls) + len(self.postcode_calls)

    @override
    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]:
        self.postcode_calls.append(postcode)
        return Attempt.succeeded(self.result) if self.result else Attempt.impossible("no result")

    @override
    async def geocode_address(self, address: str) -> Attempt[GeoPoint]:
        self.address_calls.append(address)
        return Attempt.succeeded(self.result) if self.result else Attempt.impossible("no result")

    @override
    async def reverse_geocode_town(self, lat: float, lon: float) -> Attempt[str]:
        self.reverse_calls.append((lat, lon))
        if self.reverse_town:
            return Attempt.succeeded(self.reverse_town)
        return Attempt.impossible("no town found for coordinates")


_DEFAULT_SIMON = Commute(
    person=Person(name="Simon", has_car=False),
    label="Simon (London)",
    destination=PlaceOfInterest(label="Simon (London)", address="SW1V 2QQ"),
    duration=Quantity(30, "minute"),
    daily_cost=Money("10.0", "GBP"),
)
_DEFAULT_LORENA = Commute(
    person=Person(name="Lorena", has_car=False),
    label="Lorena (London)",
    destination=PlaceOfInterest(label="Lorena (London)", address="EC3A 7LP"),
    duration=Quantity(45, "minute"),
    daily_cost=Money("12.0", "GBP"),
)
_DEFAULT_PETROL = Commute(
    person=Person(name="Simon", has_car=True),
    label="Bracknell Office (RG12 8YA)",
    destination=PlaceOfInterest(label="Bracknell Office (RG12 8YA)", address="RG12 8YA"),
    duration=Quantity(90, "minute"),
    daily_cost=Money("12.50", "GBP"),
)


class _FakeRoutePlanner(RoutePlanner):
    """Fake route planner for tests — returns a canned commute."""

    @override
    async def walk_route(self, origin, destination, max_walk):
        return Attempt.succeeded(
            Commute(
                person=Person(name="Test", has_car=False),
                label="Walk",
                destination=PlaceOfInterest(label="Dest", address=destination),
                duration=Quantity(30, "minute"),
                daily_cost=Money("0", "GBP"),
            )
        )

    @override
    async def drive_route(self, origin, destination):
        return Attempt.succeeded(
            Commute(
                person=Person(name="Test", has_car=True),
                label="Drive",
                destination=PlaceOfInterest(label="Dest", address=destination),
                duration=Quantity(20, "minute"),
                daily_cost=Money("5.50", "GBP"),
            )
        )


class _NoRoutesRouter:
    """CommuteRouter stand-in whose google_routes_post is disabled — the
    bus-route node gets no HTTP POST in unit tests (DI, no monkeypatch)."""

    google_routes_post = None


class _NoPlanTflClient:
    """TfL client factory default for unit tests — transit planning is
    impossible unless a test injects its own canned plan."""

    def __init__(self, *args, **kwargs):
        self._plan_override = None
        self._no_route_detail = ""

    async def plan(self):
        return Attempt.impossible("mocked — unit test")


class FakeSchoolLookup(SchoolLookupService):
    """School lookup that returns whatever school was passed to constructor.

    ``FakeSchoolLookup()`` returns None (no school found).  Override with
    ``FakeSchoolLookup(school=some_school)`` to return a specific school.
    """

    def __init__(self, school: School | None = None):
        self.school = school
        self.find_calls: list[tuple[str, int, str, tuple]] = []

    @override
    async def find_nearest(
        self,
        postcode: str,
        child_age: int,
        address: str = "",
        acceptable: tuple[SchoolGender, ...] = (SchoolGender.MIXED,),
    ) -> Attempt[School | None]:
        self.find_calls.append((postcode, child_age, address, acceptable))
        return Attempt.succeeded(self.school)

    @override
    async def school_commute(self, postcode: str, school: School) -> Commute | None:
        from houses.model.domain import Commute, Person, PlaceOfInterest

        return Commute(
            person=Person(name="George", has_car=False, is_child=True),
            label=school.name,
            destination=PlaceOfInterest(label=school.name, address=school.postcode),
            duration=Quantity(20, "minute"),
            daily_cost=Money("0", "GBP"),
        )


class FakeWalkability(WalkabilityService):
    def __init__(self, walk_to_town: int = 10, amenities: str = ""):
        self.walk_to_town_minutes = walk_to_town
        self.amenities = amenities

    @override
    async def enrich(self, lat: float, lng: float, address: str) -> dict[str, Any]:
        val = {"value": self.walk_to_town_minutes, "unit": "minute"} if self.walk_to_town_minutes is not None else None
        return {"walk_to_town": val, "amenities": self.amenities}


class FakeTownDesc(TownDescService):
    @override
    async def describe(self, town_name: str, postcode: str) -> Attempt[str]:
        return Attempt.succeeded("A nice town.")


class FakeEPC(EPCLookupService):
    def __init__(self, band: str = "C"):
        self.band = band
        self.calls: list[tuple[str, str]] = []

    @override
    async def lookup(self, postcode: str, address: str = "") -> Attempt[str]:
        self.calls.append((postcode, address))
        return Attempt.succeeded(self.band)


class FakeCouncilTax(CouncilTaxService):
    def __init__(self, result: CouncilTaxInfo | None = None):
        default = CouncilTaxInfo(band="D", yearly_cost=Measurement(Money("2000", "GBP"), 0.0))
        self.result = result or default

    @override
    async def lookup(self, postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]:
        return Attempt.succeeded(self.result)


class FakeRailFare(RailFareService):
    @override
    async def enrich(
        self,
        enabled: set[str] | None,
        postcode: str,
        address: str,
        simon: Commute | None,
        lorena: Commute | None,
    ) -> tuple[Commute | None, Commute | None]:
        return None, None


class FakeDriveTime(DriveTimeService):
    """Canned drive times — record which entry point was used so tests
    can assert the postcode vs location fallback path."""

    def __init__(
        self,
        minutes: int | None = 12,
        location_minutes: int | None = None,
    ):
        self.minutes = minutes
        self.location_minutes = minutes if location_minutes is None else location_minutes
        self.estimate_calls: list[tuple[str, str]] = []
        self.location_calls: list[tuple[Any, str]] = []

    @override
    async def estimate(self, origin_postcode: str, station_name: str) -> int | None:
        self.estimate_calls.append((origin_postcode, station_name))
        return self.minutes

    @override
    async def estimate_from_location(self, origin, station_name: str) -> int | None:
        self.location_calls.append((origin, station_name))
        return self.location_minutes


class FakeOAuthService(OAuthService):
    """Fake Google OAuth service for tests.

    Returns canned authorization URLs and id_info.
    """

    def __init__(
        self,
        auth_url: str = "https://accounts.google.com/o/oauth2/auth?fake",
        id_info: dict | None = None,
        verify_error: Exception | None = None,
    ):
        self.auth_url = auth_url
        self._id_info = id_info or {
            "email": "ashby@example.com",
            "email_verified": True,
            "name": "Ashby",
            "picture": "",
        }
        self._verify_error = verify_error

    @override
    def create_authorization_url(self, state: str) -> tuple[str, str]:
        return self.auth_url, "fake_code_verifier"

    @override
    def exchange_code(self, code: str, code_verifier: str, state: str) -> dict:
        return self._id_info

    @override
    async def verify_id_token(self, token: str) -> dict:
        if self._verify_error is not None:
            raise self._verify_error
        return self._id_info


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


def make_services(
    **overrides: GeocodingService
    | RoutePlanner
    | SchoolLookupService
    | WalkabilityService
    | TownDescService
    | EPCLookupService
    | CouncilTaxService
    | RailFareService
    | OAuthService
    | Any,
) -> Services:
    """Build a ``Services`` with all fakes, optionally overriding specific services.

    Default fakes return minimal data — override any service with a custom fake::

        services = make_services(
            epc_service=FakeEPC(band="B"),
            commute_router=FakeCommuteRouter(simon=None),
        )

    Override kwargs are typed against the service protocols, so a fake
    whose method signatures drift from the protocol fails type-checking
    (and is caught by basedpyright/mypy rather than at runtime).
    """
    base: dict[str, Any] = dict(
        geocoder=FakeGeocoder(),
        route_planner=_FakeRoutePlanner(),
        tfl_client_factory=_NoPlanTflClient,
        commute_router=_NoRoutesRouter(),
        school_lookup=FakeSchoolLookup(school=_DEFAULT_SCHOOL),
        walkability_service=FakeWalkability(walk_to_town=10, amenities="Shops, cafe"),
        town_desc_service=FakeTownDesc(),
        epc_service=FakeEPC(),
        council_tax_service=FakeCouncilTax(),
        rail_fare_service=FakeRailFare(),
        oauth_service=FakeOAuthService(),
    )
    base.update(overrides)
    return Services(**base)
