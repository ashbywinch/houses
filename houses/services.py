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
from typing import Any, Protocol

from google.auth.exceptions import TransportError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from google.oauth2 import id_token as google_id_token
from google_auth_oauthlib.flow import Flow

import houses.transit_route as _transit_route
from dag.attempt import Attempt
from dag.persistence import latest_node_result
from dag.user_input_node import UserInputNode
from houses.commute_router import CommuteRouter
from houses.council_tax import lookup_council_tax
from houses.council_tax_info import CouncilTaxInfo
from houses.epc import lookup_epc
from houses.geopoint import GeoPoint
from houses.location import find_nearest_town_name, geocode, geocode_address
from houses.model.domain import Commute, Person
from houses.nodes.settings import SettingsNode as SettingsInputNode
from houses.nodes.settings import make_default_persons, make_default_thresholds
from houses.nodes.settings_node import SETTING_DEFAULTS, SettingsNode
from houses.property_registry import DEFAULT_REGISTRY, PropertyRegistry
from houses.school import School
from houses.school_gender import SchoolGender
from houses.schools import SchoolLookupOptions, compute_school_commute, find_nearest
from houses.settings import settings
from houses.tfl_client import TflClient
from houses.town_desc import generate_town_description
from houses.walkability import enrich_walkability

# ── Protocols ──────────────────────────────────────────────────────────


class GeocodingService(Protocol):
    """Resolve a postcode or address to geographic coordinates,
    and reverse-geocode coordinates to the nearest town name."""

    @staticmethod
    async def geocode_postcode(postcode: str) -> Attempt[GeoPoint]: ...

    @staticmethod
    async def geocode_address(address: str) -> Attempt[GeoPoint]: ...

    @staticmethod
    async def reverse_geocode_town(lat: float, lon: float) -> Attempt[str]: ...


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


class OAuthService(Protocol):
    """Generate an OAuth authorization URL and exchange an authorization code
    for user identity information."""

    @staticmethod
# lucidlint: ignore record-shape external contract — keys owned by Google's API (review-log)
    def create_authorization_url(state: str) -> tuple[str, str]:
        """Return (authorization_url, code_verifier)."""
        ...

    @staticmethod
    def exchange_code(code: str, code_verifier: str, state: str) -> Mapping[str, Any]:
        """Exchange an authorization code for user info.
        Returns a dict with keys: email, name, picture, etc.
        """
        ...

    @staticmethod
# lucidlint: ignore record-shape external contract — keys owned by Google's API (review-log)
    async def verify_id_token(token: str) -> dict:
        """Verify a Google id_token (device flow) and return its claims."""
        ...


class WalkabilityService(Protocol):
    """Walk time to town centre and nearby amenities."""

    @staticmethod
# lucidlint: ignore record-shape external contract — keys owned by Google's API (review-log)
    async def enrich(lat: float, lng: float, address: str) -> dict[str, Any]: ...


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
# lucidlint: ignore record-shape external contract — keys owned by Google's API (review-log)
    async def enrich(
        enabled: set[str] | None,
        postcode: str,
        address: str,
        simon: Commute | None,
        lorena: Commute | None,
    ) -> tuple[Commute | None, Commute | None]: ...


class DriveTimeService(Protocol):
    """Estimate driving time from an origin to a station.

    Two entry points: a postcode (geocoded by the implementation) or
    known coordinates — park-and-ride falls back to the location-based
    estimate when a property has no postcode but does have a best
    location.
    """

    @staticmethod
    async def estimate(origin_postcode: str, station_name: str) -> int | None: ...

    @staticmethod
    async def estimate_from_location(origin, station_name: str) -> int | None: ...


class _DefaultDriveTimeService:
    @staticmethod
    async def estimate(origin_postcode: str, station_name: str) -> int | None:
        return await _transit_route._get_drive_minutes(origin_postcode, station_name)

    @staticmethod
    async def estimate_from_location(origin, station_name: str) -> int | None:
        return await _transit_route._get_drive_minutes_from_location(origin, station_name)


class _DefaultOAuthService:
    """Real Google OAuth implementation."""

    @staticmethod
# lucidlint: ignore record-shape external contract — keys owned by Google's API (review-log)
    def create_authorization_url(state: str) -> tuple[str, str]:
        client_config = {
# lucidlint: ignore record-shape external contract — keys owned by Google's API (review-log)
            "web": {
                "client_id": settings.web_client_id,
                "client_secret": settings.web_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.public_url.rstrip("/") + "/api/auth/callback"],
            }
        }
        flow = Flow.from_client_config(
            client_config,
            scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
            ],
        )
        flow.redirect_uri = settings.public_url.rstrip("/") + "/api/auth/callback"
        authorization_url, _state_from_flow = flow.authorization_url(
            access_type="online",
            include_granted_scopes="false",
            state=state,
        )
        code_verifier: str = getattr(flow, "code_verifier", None) or ""  # type: ignore[arg-type]  # google-auth sets code_verifier inside authorization_url() (PKCE); the library types don't declare the attribute, so getattr's None default is flagged — at this point in the flow it is always populated
        return authorization_url, code_verifier

    @staticmethod
    def exchange_code(code: str, code_verifier: str, state: str) -> Mapping[str, Any]:

        client_config = {
# lucidlint: ignore record-shape external contract — keys owned by Google's API (review-log)
            "web": {
                "client_id": settings.web_client_id,
                "client_secret": settings.web_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.public_url.rstrip("/") + "/api/auth/callback"],
            }
        }
        flow = Flow.from_client_config(
            client_config,
            scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
            ],
        )
        flow.redirect_uri = settings.public_url.rstrip("/") + "/api/auth/callback"
        flow.code_verifier = code_verifier
        flow.fetch_token(code=code)
        # google_auth_oauthlib is untyped and its inferred Credentials union
        # hides the runtime id_token property — read it defensively.
        id_token_str = getattr(flow.credentials, "id_token", None)
        if not id_token_str:
            raise ValueError("OAuth exchange returned no id_token")
        id_info = id_token.verify_oauth2_token(
            id_token_str,
            google_requests.Request(),
            settings.web_client_id,
        )
        return id_info

    @staticmethod
# lucidlint: ignore record-shape external contract — keys owned by Google's API (review-log)
    async def verify_id_token(token: str) -> dict:
        """Verify a Google id_token (device flow) and return its claims.

        Bound strictly to the device-flow client: a web-flow id_token (easy
        to leak from a browser context) must not be replayable at this
        headless session-minting endpoint.

        Runs in a thread: cert-fetch + verification are blocking network I/O
        and must not stall the event loop. Bounded by wait_for so a stuck
        cert fetch surfaces as TransportError (→ endpoint 503) instead of
        hanging the request forever.
        """

        if not settings.device_client_id:
            raise ValueError("device_client_id not configured for device-flow login")

# lucidlint: ignore record-shape external contract — keys owned by Google's API (review-log)
        def _verify_in_thread() -> dict:
            # Build the cert-fetch session inside the worker thread so it is
            # created and used in one thread — requests.Session isn't
            # guaranteed thread-safe across thread boundaries.
            return dict(
                google_id_token.verify_oauth2_token(
                    token,
                    google_requests.Request(),
                    settings.device_client_id,
                )
            )

        try:
            verified = await asyncio.wait_for(
                asyncio.to_thread(_verify_in_thread),
                timeout=10,
            )
        except TimeoutError:
            raise TransportError("Google id_token verification timed out after 10s") from None
        return verified


# Settings sources are cached by node_id so that the same UserInputNode
# instance is returned on every Services() construction.  This means
# a PATCH to /api/settings/financial updates the canonical node that
# all PropertyNodes reference, without needing a server restart.
# lucidlint: ignore global-state bounded module cache/state — single writer, deliberate
SETTINGS_SOURCE_CACHE: dict[str, UserInputNode] = {}


def _reset_settings_cache():
    """Clear the settings source cache for test isolation."""
    SETTINGS_SOURCE_CACHE.clear()


def _make_settings_source(
    node_id: str,
    value_type: type,
    default_factory,
    latest_node_result_fn: Callable[[str], dict[str, Any] | None] | None = None,
):
    if node_id in SETTINGS_SOURCE_CACHE:
        return SETTINGS_SOURCE_CACHE[node_id]
    node = SettingsInputNode(node_id, value_type)
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
    SETTINGS_SOURCE_CACHE[node_id] = node
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


class _DefaultRoutePlanner:
    """Default route planner — wraps CommuteRouter."""

    @staticmethod
    async def walk_route(origin: GeoPoint, destination: str, max_walk: int) -> Attempt[Commute]:
        return await CommuteRouter()._google_route_commute(origin, destination, "WALK", max_walk)

    @staticmethod
    async def drive_route(origin: GeoPoint, destination: str) -> Attempt[Commute]:
        return await CommuteRouter()._google_route_commute(origin, destination, "DRIVE")


def _default_commute_router() -> Any:
    """The routing aggregate the DAG builder reads."""
    return CommuteRouter()


def _default_tfl_client_factory() -> Callable[..., Any]:
    """The default TfL client factory — the real client class."""
    return TflClient


class _DefaultSchoolLookup:
    @staticmethod
    async def find_nearest(
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

    @staticmethod
    async def school_commute(postcode: str, school: School) -> Commute | None:
        return await compute_school_commute(postcode, school)


class _DefaultWalkability:
# lucidlint: ignore record-shape external contract — keys owned by Google's API (review-log)
    @staticmethod
    async def enrich(lat: float, lng: float, address: str) -> dict[str, Any]:
        return await enrich_walkability(lat, lng, address)


class _DefaultTownDesc:
    @staticmethod
    async def describe(town_name: str, postcode: str) -> Attempt[str]:
        return await generate_town_description(town_name, postcode)


class _DefaultEPCLookup:
    @staticmethod
    async def lookup(postcode: str, address: str = "") -> Attempt[str]:
        return await lookup_epc(postcode, address)


class _DefaultCouncilTax:
    @staticmethod
    async def lookup(postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]:
        return await lookup_council_tax(postcode, address)


class _DefaultRailFare:
# lucidlint: ignore record-shape external contract — keys owned by Google's API (review-log)
    @staticmethod
    async def enrich(
        enabled: set[str] | None,
        postcode: str,
        address: str,
        simon: Commute | None,
        lorena: Commute | None,
    ) -> tuple[Commute | None, Commute | None]:
        # NR fare enrichment is now handled by RailFareNode in the DAG pipeline
        return simon, lorena


# ── DI Container ──────────────────────────────────────────────────────


def _default_auth_enabled() -> bool:
    return bool(settings.web_client_id)


@dataclasses.dataclass
class Services:
    auth_enabled: bool = dataclasses.field(default_factory=_default_auth_enabled)
    geocoder: GeocodingService = dataclasses.field(default_factory=_DefaultGeocoder)
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
    oauth_service: OAuthService = dataclasses.field(default_factory=_DefaultOAuthService)
    # Test seam: injected reader for the settings persistence lookup
    # (None = use dag.persistence.latest_node_result).
    latest_node_result_fn: Callable[[str], dict[str, Any] | None] | None = None
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
    # Per-request mutable state (lazily initialized by accessors)
    geo_state: Any | None = None
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
            SETTINGS_SOURCE_CACHE.clear()
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
        # Create individual setting nodes
        if not self.setting_nodes:
            self.setting_nodes = {}
            for node_id, (val_type, default_fn) in SETTING_DEFAULTS.items():
                self.setting_nodes[node_id] = _make_settings_source(
                    node_id,
                    val_type,
                    default_fn,
                    self.latest_node_result_fn,
                )
        # Bind the container into the default geocoder so it threads services
        # explicitly instead of re-resolving the request container.
        if isinstance(self.geocoder, _DefaultGeocoder):
            self.geocoder._services = self

    @property
    def settings_view(self):
        """Lazy SettingsNode aggregate for API use.

        Reads from individual setting nodes and returns the same dict
        shape as financial_source did. Created once per Services instance.
        """
        if self._settings_view is None:
            self._settings_view = SettingsNode(
                "financial_aggregate",
                setting_nodes=self.setting_nodes,
            )
        return self._settings_view

    @staticmethod
    async def tfl_plan(origin: str, destination: str, label: str) -> Attempt[Commute]:
        """Plan a TfL transit route. Wraps the real client for DI."""
        return await TflClient(origin, destination, label).plan()
