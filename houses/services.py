"""Service protocols and dependency injection container.

Each protocol defines a boundary that enrichment modules implement.
The ``Services`` dataclass bundles all services with real defaults.

Tests create ``FakeServices`` (or a partial override) to replace
specific services without monkeypatching.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, cast

from google.auth.exceptions import TransportError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from google.oauth2 import id_token as google_id_token
from google_auth_oauthlib.flow import Flow
from pydantic import SerializerFunctionWrapHandler, model_serializer

import houses.transit_route as _transit_route
from dag.attempt import Attempt
from dag.persistence import latest_node_result
from dag.user_input_node import UserInputNode
from houses.commute_router import CommuteRouter
from houses.council_tax import lookup_council_tax
from houses.council_tax_info import CouncilTaxInfo
from houses.epc import lookup_epc
from houses.geopoint import GeoPoint
from houses.location import (
    WalkabilityFns,
    find_nearest_town_name,
    geocode,
    geocode_address,
)
from houses.location import (
    reverse_geocode_postcode as _reverse_geocode_postcode,
)
from houses.model.domain import Commute, Person
from houses.nodes.settings import PersonsSourceNode, make_default_persons, make_default_thresholds
from houses.nodes.settings import SettingsNode as SettingsInputNode
from houses.nodes.settings_node import SettingsNode
from houses.property_registry import DEFAULT_REGISTRY, PropertyRegistry
from houses.school import School
from houses.school_gender import SchoolGender
from houses.schools import SchoolLookupOptions, compute_school_commute, find_nearest
from houses.settings import settings
from houses.stations import Station, StationRegistry
from houses.tfl_client import TflClient
from houses.town_desc import generate_town_description
from houses.walkability import Walkability, enrich_walkability
from serialization import SerializableAsDict

# ── Protocols ──────────────────────────────────────────────────────────


class GeocodingService(Protocol):
    """Resolve a postcode or address to geographic coordinates,
    and reverse-geocode coordinates to the nearest town name."""

    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]: ...

    async def geocode_address(self, address: str) -> Attempt[GeoPoint]: ...

    @staticmethod
    async def reverse_geocode_town(self, lat: float, lon: float) -> Attempt[str]: ...

    async def reverse_geocode_postcode(self, lat: float, lon: float) -> Attempt[str]: ...


class RoutePlanner(Protocol):
    """Plan a single-mode route (walk or drive)."""

    @staticmethod
    async def walk_route(origin: GeoPoint, destination: str, max_walk: int) -> Attempt[Commute]: ...

    @staticmethod
    async def drive_route(origin: GeoPoint, destination: str) -> Attempt[Commute]: ...


class SchoolLookupService(Protocol):
    """Find nearest suitable school and compute its commute."""

    @staticmethod
    async def find_nearest(
        postcode: str,
        child_age: int,
        address: str = "",
        acceptable: tuple[SchoolGender, ...] = (SchoolGender.MIXED,),
    ) -> Attempt[School | None]: ...

    @staticmethod
    async def school_commute(postcode: str, school: School) -> Commute | None: ...


class _GoogleClientConfig(SerializableAsDict):
    """The Google OAuth web-client config google_auth_oauthlib expects.

    The library's config object nests the client's five fields under ``web`` —
    a change of STRUCTURE, not of a key, so the whole record serialises through
    one wrap serializer (coding-standards.md → *Boundary records*).
    """

    client_id: str
    client_secret: str
    auth_uri: str
    token_uri: str
    redirect_uris: list[str]

    @model_serializer(mode="wrap")
    def _as_client_config(self, handler: SerializerFunctionWrapHandler) -> Mapping[str, Any]:
        """The library's ``{"web": {...}}`` config object."""
        return {"web": handler(self)}


@dataclass(frozen=True)
class GoogleUserInfo:
    """The verified Google identity claims the auth flow consumes."""

    email: str
    name: str
    picture: str
    email_verified: bool = False


@dataclass(frozen=True)
class _AuthorizationUrl:
    """The OAuth authorization URL plus the PKCE code verifier."""

    url: str
    code_verifier: str


@dataclass(frozen=True)
class _RailFarePair:
    """The rail-fare-enriched commute pair (Simon, Lorena)."""

    simon: Commute | None
    lorena: Commute | None


def _google_client_config(redirect_uri: str) -> _GoogleClientConfig:
    """The Google client config for ONE redirect URI.

    The caller supplies the redirect (see `houses.web.auth._oauth_redirect_uri`):
    the rollout reviews a box at the review hostname before it serves
    production, and the box already carries its final production URL — a login
    must come back to the hostname the user is actually on.
    """
    return _GoogleClientConfig(
        client_id=settings.web_client_id,
        client_secret=settings.web_client_secret,
        auth_uri="https://accounts.google.com/o/oauth2/auth",
        token_uri="https://oauth2.googleapis.com/token",
        redirect_uris=[redirect_uri],
    )


class OAuthService(Protocol):
    """Generate an OAuth authorization URL and exchange an authorization code
    for user identity information."""

    @staticmethod
    def create_authorization_url(state: str, redirect_uri: str) -> _AuthorizationUrl:
        """Return the authorization URL and its PKCE code verifier."""
        ...

    @staticmethod
    def exchange_code(code: str, code_verifier: str, state: str, redirect_uri: str) -> GoogleUserInfo:
        """Exchange an authorization code for user info."""
        ...

    @staticmethod
    async def verify_id_token(token: str) -> GoogleUserInfo:
        """Verify a Google id_token (device flow) and return its claims."""
        ...


class WalkabilityService(Protocol):
    """Walk time to town centre and nearby amenities."""

    @staticmethod
    async def enrich(lat: float, lng: float, address: str) -> Walkability: ...


class TownDescService(Protocol):
    """LLM-generated description of a town or area."""

    @staticmethod
    async def describe(town_name: str, postcode: str) -> Attempt[str]: ...


class EPCLookupService(Protocol):
    """Energy Performance Certificate band lookup."""

    @staticmethod
    async def lookup(postcode: str, address: str = "") -> Attempt[str]: ...


class CouncilTaxService(Protocol):
    """Council tax band and yearly cost lookup."""

    @staticmethod
    async def lookup(postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]: ...


class RailFareService(Protocol):
    """National Rail fare fallback for commute costs."""

    @staticmethod
    async def enrich(
        enabled: set[str] | None,
        postcode: str,
        address: str,
        simon: Commute | None,
        lorena: Commute | None,
    ) -> _RailFarePair: ...


class StationLookupService(Protocol):
    """Find a station by name — the destination side of a drive estimate."""

    @staticmethod
    def find(name: str) -> Station | None: ...


class DriveTimeService(Protocol):
    """Estimate driving time from an origin to a station.

    Two entry points: a postcode (geocoded by the implementation) or
    known coordinates — park-and-ride falls back to the location-based
    estimate when a property has no postcode but does have a best
    location.
    """

    async def estimate(self, origin_postcode: str, station_name: str) -> int | None: ...

    async def estimate_from_location(self, origin, station_name: str) -> int | None: ...




class _DefaultDriveTimeService:
    """Real drive-estimate wrapper — forwards to transit_route, threading
    the container so every estimate runs the same tested path."""

    def __init__(self, services: Services | None = None):
        self._services: Services | None = services

    async def estimate(self, origin_postcode: str, station_name: str) -> int | None:
        return await _transit_route._get_drive_minutes(origin_postcode, station_name, services=self._services)

    async def estimate_from_location(self, origin, station_name: str) -> int | None:
        return await _transit_route._get_drive_minutes_from_location(origin, station_name, services=self._services)


def _default_oauth_authorization_url(state: str, redirect_uri: str, scopes: list[str]) -> _AuthorizationUrl:
    """Generate the Google authorization URL (the operation body)."""
    client_config = _google_client_config(redirect_uri).to_dict()
    flow = Flow.from_client_config(
        client_config,
        scopes=scopes,
    )
    flow.redirect_uri = redirect_uri
    authorization_url, _state_from_flow = flow.authorization_url(
        access_type="online",
        include_granted_scopes="false",
        state=state,
    )
    code_verifier: str = getattr(flow, "code_verifier", None) or ""  # type: ignore[arg-type]  # google-auth sets code_verifier inside authorization_url() (PKCE); the library types don't declare the attribute, so getattr's None default is flagged — at this point in the flow it is always populated
    return _AuthorizationUrl(url=authorization_url, code_verifier=code_verifier)


def _default_oauth_exchange_code(
    code: str, code_verifier: str, state: str, redirect_uri: str, scopes: list[str]
) -> GoogleUserInfo:
    """Exchange an authorization code for verified Google claims."""
    client_config = _google_client_config(redirect_uri).to_dict()
    flow = Flow.from_client_config(
        client_config,
        scopes=scopes,
    )
    flow.redirect_uri = redirect_uri
    flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    # google_auth_oauthlib is untyped and its inferred Credentials union
    # hides the runtime id_token property — read it defensively.
    id_token_str = getattr(flow.credentials, "id_token", None)
    if not id_token_str:
        raise ValueError("OAuth exchange returned no id_token")
    claims = id_token.verify_oauth2_token(
        id_token_str,
        google_requests.Request(),
        settings.web_client_id,
    )
    return GoogleUserInfo(
        email=claims.get("email", ""),
        name=claims.get("name", ""),
        picture=claims.get("picture", ""),
        email_verified=bool(claims.get("email_verified", False)),
    )


async def _default_oauth_verify_id_token(token: str, device_client_id: str) -> GoogleUserInfo:
    """Verify a Google id_token (device flow) and return its claims.

    Bound strictly to the device-flow client: a web-flow id_token (easy
    to leak from a browser context) must not be replayable at this
    headless session-minting endpoint.

    Runs in a thread: cert-fetch + verification are blocking network I/O
    and must not stall the event loop. Bounded by wait_for so a stuck
    cert fetch surfaces as TransportError (→ endpoint 503) instead of
    hanging the request forever.
    """

    if not device_client_id:
        raise ValueError("device_client_id not configured for device-flow login")

    def _verify_in_thread() -> GoogleUserInfo:
        # Build the cert-fetch session inside the worker thread so it is
        # created and used in one thread — requests.Session isn't
        # guaranteed thread-safe across thread boundaries.
        claims = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            device_client_id,
        )
        return GoogleUserInfo(
            email=claims.get("email", ""),
            name=claims.get("name", ""),
            picture=claims.get("picture", ""),
            email_verified=bool(claims.get("email_verified", False)),
        )

    try:
        verified = await asyncio.wait_for(
            asyncio.to_thread(_verify_in_thread),
            timeout=10,
        )
    except TimeoutError:
        raise TransportError("Google id_token verification timed out after 10s") from None
    return verified


class _DefaultOAuthService:
    """Container default — owns the Google client identity (the scopes
    and device-flow client id the auth flows use), dispatching to the
    module functions."""

    def __init__(self) -> None:
        self._scopes: list[str] = [
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ]
        self._device_client_id: str = settings.device_client_id

    def create_authorization_url(self, state: str, redirect_uri: str) -> _AuthorizationUrl:
        return _default_oauth_authorization_url(state, redirect_uri, self._scopes)

    def exchange_code(self, code: str, code_verifier: str, state: str, redirect_uri: str) -> GoogleUserInfo:
        return _default_oauth_exchange_code(code, code_verifier, state, redirect_uri, self._scopes)

    async def verify_id_token(self, token: str) -> GoogleUserInfo:
        return await _default_oauth_verify_id_token(token, self._device_client_id)


# Settings sources are cached by node_id (on Services) so that the same
# UserInputNode instance is returned on every Services() construction.
def _reset_settings_cache():
    """Clear the settings source cache for test isolation."""
    Services.SETTINGS_SOURCE_CACHE.clear()


def _make_settings_source(
    node_id: str,
    value_type: type,
    default_factory,
    latest_node_result_fn: Callable[[str], Mapping[str, Any] | None] | None = None,
):
    if node_id in Services.SETTINGS_SOURCE_CACHE:
        return Services.SETTINGS_SOURCE_CACHE[node_id]
    node = PersonsSourceNode(node_id, value_type) if node_id == "persons" else SettingsInputNode(node_id, value_type)
    if latest_node_result_fn is None:
        latest_node_result_fn = latest_node_result
    persisted = latest_node_result_fn(node_id)
    if persisted and persisted.get("status") == "succeeded":
        source_label = persisted.get("source_label", "db")
        if source_label in ("tests", "test"):
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
    Services.SETTINGS_SOURCE_CACHE[node_id] = node
    return node


# ── Default implementations (thin wrappers around real modules) ────────
#
# houses.services_provider does NOT import houses.services (it loads it lazily
# via importlib), so houses.location and houses.commute_router can import
# get_services at module top and these wrappers can import their modules here
# without a cycle. The default geocoder/route-planner receive the Services
# container and thread it into the location functions.


class _DefaultGeocoder:
    """Real geocoder wrapper — forwards to houses.location, threading the
    services container so geocode state/cache live on the injected instance."""

    def __init__(self, services: Services | None = None):
        self._services: Services | None = services

    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]:
        return await geocode(postcode, services=self._services)

    async def geocode_address(self, address: str) -> Attempt[GeoPoint]:
        return await geocode_address(address, services=self._services)

    @staticmethod
    async def reverse_geocode_town(lat: float, lon: float) -> Attempt[str]:
        return await find_nearest_town_name(lat, lon)

    async def reverse_geocode_postcode(self, lat: float, lon: float) -> Attempt[str]:
        postcode = await _reverse_geocode_postcode(lat, lon, services=self._services)
        if postcode:
            return Attempt.succeeded(postcode)
        return Attempt.impossible("no postcode found for coordinates")


class _DefaultRoutePlanner:
    """Container default — owns the CommuteRouter it dispatches to (the
    router's DI constructor carries the route/client seams)."""

    def __init__(self, router: CommuteRouter | None = None):
        self._router = router or CommuteRouter()

    async def walk_route(self, origin: GeoPoint, destination: str, max_walk: int) -> Attempt[Commute]:
        return await self._router._google_route_commute(origin, destination, "WALK", max_walk)

    async def drive_route(self, origin: GeoPoint, destination: str) -> Attempt[Commute]:
        return await self._router._google_route_commute(origin, destination, "DRIVE")


def _default_commute_router() -> Any:
    """The routing aggregate the DAG builder reads."""
    return CommuteRouter()


def _default_tfl_client_factory() -> Callable[..., Any]:
    """The default TfL client factory — the real client class."""
    return TflClient


async def _default_school_find_nearest(
    postcode: str,
    child_age: int,
    address: str = "",
    acceptable: tuple[SchoolGender, ...] = (SchoolGender.MIXED,),
) -> Attempt[School | None]:
    return await find_nearest(
        postcode,
        child_age=child_age,
        address=address,
        options=SchoolLookupOptions(acceptable=acceptable),
    )


async def _default_school_commute(postcode: str, school: School) -> Commute | None:
    return await compute_school_commute(postcode, school)


class _DefaultSchoolLookup:
    """Container default — owns the lookup functions it dispatches to
    (constructor-DI, the house no-monkeypatch seam for fakes)."""

    def __init__(self, find_nearest_fn=None, school_commute_fn=None):
        self._find_nearest = find_nearest_fn or _default_school_find_nearest
        self._school_commute = school_commute_fn or _default_school_commute

    async def find_nearest(
        self,
        postcode: str,
        child_age: int,
        address: str = "",
        acceptable: tuple[SchoolGender, ...] = (SchoolGender.MIXED,),
    ) -> Attempt[School | None]:
        return await self._find_nearest(
            postcode, child_age, address=address, acceptable=acceptable
        )

    async def school_commute(self, postcode: str, school: School) -> Commute | None:
        return await self._school_commute(postcode, school)


class _DefaultWalkability:
    """Container default — owns the function-param injection seams
    (``WalkabilityFns``; walkability's no-monkeypatch DI surface)."""

    def __init__(self, fns: WalkabilityFns | None = None):
        self._fns = fns

    async def enrich(self, lat: float, lng: float, address: str) -> Walkability:
        return await enrich_walkability(lat, lng, address, self._fns)


class _DefaultTownDesc:
    """Container default — owns the describe function it dispatches to
    (constructor-DI, the house no-monkeypatch seam for fakes)."""

    def __init__(self, describe_fn=None):
        self._describe = describe_fn or generate_town_description

    async def describe(self, town_name: str, postcode: str) -> Attempt[str]:
        return await self._describe(town_name, postcode)


class _DefaultEPCLookup:
    """Container default — owns the lookup function it dispatches to
    (constructor-DI, the house no-monkeypatch seam for fakes)."""

    def __init__(self, lookup_fn=None):
        self._lookup = lookup_fn or lookup_epc

    async def lookup(self, postcode: str, address: str = "") -> Attempt[str]:
        return await self._lookup(postcode, address)


class _DefaultCouncilTax:
    """Container default — owns the lookup function it dispatches to
    (constructor-DI, the house no-monkeypatch seam for fakes)."""

    def __init__(self, lookup_fn=None):
        self._lookup = lookup_fn or lookup_council_tax

    async def lookup(self, postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]:
        return await self._lookup(postcode, address)


def _default_rail_fare_enrich(
    enabled: set[str] | None,
    postcode: str,
    address: str,
    simon: Commute | None,
    lorena: Commute | None,
) -> _RailFarePair:
    # NR fare enrichment is now handled by RailFareNode in the DAG pipeline
    return _RailFarePair(simon=simon, lorena=lorena)


class _DefaultRailFare:
    """Container default — owns the enrich function it dispatches to
    (constructor-DI, the house no-monkeypatch seam for fakes)."""

    def __init__(self, enrich_fn=None):
        self._enrich = enrich_fn or _default_rail_fare_enrich

    async def enrich(
        self,
        enabled: set[str] | None,
        postcode: str,
        address: str,
        simon: Commute | None,
        lorena: Commute | None,
    ) -> _RailFarePair:
        return self._enrich(enabled, postcode, address, simon, lorena)

# ── DI Container ──────────────────────────────────────────────────────


def _default_auth_enabled() -> bool:
    return bool(settings.web_client_id)


@dataclasses.dataclass
class Services:
    # Settings sources are cached by node_id so that the SAME UserInputNode
    # is returned on every Services() construction — a PATCH to
    # /api/settings/financial updates the canonical node that all
    # PropertyNodes reference, without needing a server restart.
    SETTINGS_SOURCE_CACHE: ClassVar[dict[str, UserInputNode]] = {}
    auth_enabled: bool = dataclasses.field(default_factory=_default_auth_enabled)
    geocoder: GeocodingService = dataclasses.field(default_factory=lambda: cast(GeocodingService, _DefaultGeocoder()))
    route_planner: RoutePlanner = dataclasses.field(default_factory=_DefaultRoutePlanner)
    tfl_client_factory: Callable[..., Any] = dataclasses.field(default_factory=_default_tfl_client_factory)
    commute_router: Any = dataclasses.field(default_factory=_default_commute_router)
    school_lookup: SchoolLookupService = dataclasses.field(default_factory=_DefaultSchoolLookup)
    walkability_service: WalkabilityService = dataclasses.field(default_factory=_DefaultWalkability)
    town_desc_service: TownDescService = dataclasses.field(default_factory=_DefaultTownDesc)
    epc_service: EPCLookupService = dataclasses.field(default_factory=_DefaultEPCLookup)
    council_tax_service: CouncilTaxService = dataclasses.field(default_factory=_DefaultCouncilTax)
    rail_fare_service: RailFareService = dataclasses.field(default_factory=_DefaultRailFare)
    drive_time_service: DriveTimeService = dataclasses.field(default_factory=_DefaultDriveTimeService)
    station_lookup: StationLookupService = dataclasses.field(default_factory=StationRegistry)
    oauth_service: OAuthService = dataclasses.field(default_factory=_DefaultOAuthService)
    # Test seam: injected reader for the settings persistence lookup
    # (None = use dag.persistence.latest_node_result).
    latest_node_result_fn: Callable[[str], Mapping[str, Any] | None] | None = None
    persons_source: UserInputNode[list[Person]] = dataclasses.field(
        default_factory=lambda: _make_settings_source("persons", list[Person], make_default_persons)
    )
    # Individual financial setting nodes (created in __post_init__);
    # the API reads them through the aggregate (settings_view).
    setting_nodes: dict[str, UserInputNode] = dataclasses.field(default_factory=dict)
    # SettingsNode aggregate (lazily created, accessed via settings_view)
    _settings_view: Any | None = dataclasses.field(default=None)
    commute_thresholds_source: UserInputNode[dict] = dataclasses.field(
        default_factory=lambda: _make_settings_source("commute_thresholds", dict, make_default_thresholds)
    )
    # The what-if boundary marker: the ISO timestamp of the persons
    # attempt that was live when the what-if was applied (empty = no
    # what-if active). Persisted by the same settings-source mechanism.
    whatif_started_at: UserInputNode[str] = dataclasses.field(
        default_factory=lambda: _make_settings_source("whatif_started_at", str, lambda: "")
    )
    # Per-request mutable state (lazily initialized by accessors)
    geo_cache: dict | None = None
    bus_fare_registry: Any | None = None
    rail_fare_registry: Any | None = None
    # The live PropertyNodes registry — shared app-wide by default (startup
    # seeding and per-request reads must see one registry), injectable per test.
    property_registry: PropertyRegistry = dataclasses.field(default_factory=lambda: DEFAULT_REGISTRY)

    def __post_init__(self):
        if self.latest_node_result_fn is not None:
            # The dataclass field factories ran with the real reader —
            # invalidate the whole settings-source cache and rebuild every
            # source against the injected reader, or the cached financial
            # nodes silently ignore the fake.
            Services.SETTINGS_SOURCE_CACHE.clear()
            self.persons_source = _make_settings_source(
                "persons",
                list[Person],
                make_default_persons,
                self.latest_node_result_fn,
            )
            self.commute_thresholds_source = _make_settings_source(
                "commute_thresholds",
                dict,
                make_default_thresholds,
                self.latest_node_result_fn,
            )
            self.whatif_started_at = _make_settings_source(
                "whatif_started_at",
                str,
                lambda: "",
                self.latest_node_result_fn,
            )
        # Create individual setting nodes
        if not self.setting_nodes:
            self.setting_nodes = {
                node_id: _make_settings_source(
                    node_id,
                    val_type,
                    default_fn,
                    self.latest_node_result_fn,
                )
                for node_id, (val_type, default_fn) in SettingsNode.SETTING_DEFAULTS.items()
            }
        # Bind the container into the default geocoder so it threads services
        # explicitly instead of re-resolving the request container.
        if isinstance(self.geocoder, _DefaultGeocoder):
            self.geocoder._services = self
        if isinstance(self.drive_time_service, _DefaultDriveTimeService):
            self.drive_time_service._services = self

    @property
    def settings_view(self):
        """Lazy SettingsNode aggregate for API use.

        Reads from individual setting nodes and returns the same dict
        shape as financial_source did. Created once per Services instance.
        """
        view = self._settings_view
        if view is None:
            view = self._settings_view = SettingsNode(
                "financial_aggregate",
                setting_nodes=self.setting_nodes,
            )
        return view

    @staticmethod
    async def tfl_plan(origin: str, destination: str, label: str) -> Attempt[Commute]:
        """Plan a TfL transit route. Wraps the real client for DI."""
        return await TflClient(origin, destination, label).plan()
