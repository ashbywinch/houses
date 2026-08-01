"""Service protocols and dependency injection container.

Each protocol defines a boundary that enrichment modules implement.
The ``Services`` dataclass bundles all services with real defaults.

Tests create ``FakeServices`` (or a partial override) to replace
specific services without monkeypatching.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any, Protocol

from dag.attempt import Attempt
from dag.persistence import latest_node_result
from dag.user_input_node import UserInputNode
from houses.config import settings
from houses.council_tax import lookup_council_tax
from houses.council_tax_info import CouncilTaxInfo
from houses.epc import lookup_epc
from houses.geo import GeoPoint
from houses.location import _geocode_address, find_nearest_town_name, geocode
from houses.model.domain import Commute, Person
from houses.nodes.settings import make_default_financials, make_default_persons, make_default_thresholds
from houses.routing import CommuteRouter
from houses.school import School
from houses.school_gender import SchoolGender
from houses.schools import compute_school_commute, find_nearest
from houses.town_desc import generate_town_description
from houses.walkability import enrich_walkability

# ── Protocols ──────────────────────────────────────────────────────────


class GeocodingService(Protocol):
    """Resolve a postcode or address to geographic coordinates,
    and reverse-geocode coordinates to the nearest town name."""

    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]: ...

    async def geocode_address(self, address: str) -> Attempt[GeoPoint]: ...

    async def reverse_geocode_town(self, lat: float, lon: float) -> Attempt[str]: ...


class RoutePlanner(Protocol):
    """Plan a single-mode route (walk or drive)."""

    async def walk_route(self, origin: GeoPoint, destination: str, max_walk: int) -> Attempt[Commute]: ...

    async def drive_route(self, origin: GeoPoint, destination: str) -> Attempt[Commute]: ...


class SchoolLookupService(Protocol):
    """Find nearest suitable school and compute its commute."""

    async def find_nearest(
        self,
        postcode: str,
        child_age: int,
        address: str = "",
        acceptable: tuple[SchoolGender, ...] = (SchoolGender.MIXED,),
    ) -> Attempt[School | None]: ...

    async def school_commute(self, postcode: str, school: School) -> Commute | None: ...


class OAuthService(Protocol):
    """Generate an OAuth authorization URL and exchange an authorization code
    for user identity information."""

    def create_authorization_url(self, state: str) -> tuple[str, str]:
        """Return (authorization_url, code_verifier)."""
        ...

    def exchange_code(self, code: str, code_verifier: str, state: str) -> dict:
        """Exchange an authorization code for user info.
        Returns a dict with keys: email, name, picture, etc.
        """
        ...

    async def verify_id_token(self, token: str) -> dict:
        """Verify a Google id_token (device flow) and return its claims."""
        ...


class WalkabilityService(Protocol):
    """Walk time to town centre and nearby amenities."""

    async def enrich(self, lat: float, lng: float, address: str) -> dict[str, Any]: ...


class TownDescService(Protocol):
    """LLM-generated description of a town or area."""

    async def describe(self, town_name: str, postcode: str) -> Attempt[str]: ...


class EPCLookupService(Protocol):
    """Energy Performance Certificate band lookup."""

    async def lookup(self, postcode: str, address: str = "") -> Attempt[str]: ...


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


class _DefaultOAuthService:
    """Real Google OAuth implementation."""

    def create_authorization_url(self, state: str) -> tuple[str, str]:
        from google_auth_oauthlib.flow import Flow

        client_config = {
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
        code_verifier: str = getattr(flow, "code_verifier", None) or ""  # type: ignore[arg-type]
        return authorization_url, code_verifier

    def exchange_code(self, code: str, code_verifier: str, state: str) -> dict:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
        from google_auth_oauthlib.flow import Flow

        client_config = {
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
        id_info = id_token.verify_oauth2_token(
            flow.credentials.id_token,
            google_requests.Request(),
            settings.web_client_id,
        )
        return id_info

    async def verify_id_token(self, token: str) -> dict:
        """Verify a Google id_token (device flow) and return its claims.

        Bound strictly to the device-flow client: a web-flow id_token (easy
        to leak from a browser context) must not be replayable at this
        headless session-minting endpoint.

        Runs in a thread: cert-fetch + verification are blocking network I/O
        and must not stall the event loop.
        """
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        if not settings.device_client_id:
            raise ValueError("device_client_id not configured for device-flow login")
        return dict(
            await asyncio.to_thread(
                google_id_token.verify_oauth2_token,
                token,
                google_requests.Request(),
                settings.device_client_id,
            )
        )


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
    _SETTINGS_SOURCE_CACHE[node_id] = node
    return node


# ── Default implementations (thin wrappers around real modules) ────────


class _DefaultGeocoder:
    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]:
        return await geocode(postcode)

    async def geocode_address(self, address: str) -> Attempt[GeoPoint]:
        return await _geocode_address(address)

    async def reverse_geocode_town(self, lat: float, lon: float) -> Attempt[str]:
        return await find_nearest_town_name(lat, lon)


class _DefaultRoutePlanner:
    """Default route planner — wraps CommuteRouter."""

    async def walk_route(self, origin: GeoPoint, destination: str, max_walk: int) -> Attempt[Commute]:

        return await CommuteRouter()._google_route_commute(origin, destination, "WALK", max_walk)

    async def drive_route(self, origin: GeoPoint, destination: str) -> Attempt[Commute]:

        return await CommuteRouter()._google_route_commute(origin, destination, "DRIVE")


class _DefaultSchoolLookup:
    async def find_nearest(
        self,
        postcode: str,
        child_age: int,
        address: str = "",
        acceptable: tuple[SchoolGender, ...] = (SchoolGender.MIXED,),
    ) -> Attempt[School | None]:
        return await find_nearest(postcode, child_age=child_age, address=address, acceptable=acceptable)

    async def school_commute(self, postcode: str, school: School) -> Commute | None:
        return await compute_school_commute(postcode, school)


class _DefaultWalkability:
    async def enrich(self, lat: float, lng: float, address: str) -> dict[str, Any]:
        return await enrich_walkability(lat, lng, address)


class _DefaultTownDesc:
    async def describe(self, town_name: str, postcode: str) -> Attempt[str]:
        return await generate_town_description(town_name, postcode)


class _DefaultEPCLookup:
    async def lookup(self, postcode: str, address: str = "") -> Attempt[str]:
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


def _default_auth_enabled() -> bool:
    return bool(settings.web_client_id)


@dataclasses.dataclass
class Services:
    auth_enabled: bool = dataclasses.field(default_factory=_default_auth_enabled)
    geocoder: GeocodingService = dataclasses.field(default_factory=_DefaultGeocoder)
    route_planner: RoutePlanner = dataclasses.field(default_factory=_DefaultRoutePlanner)
    school_lookup: SchoolLookupService = dataclasses.field(default_factory=_DefaultSchoolLookup)
    walkability_service: WalkabilityService = dataclasses.field(default_factory=_DefaultWalkability)
    town_desc_service: TownDescService = dataclasses.field(default_factory=_DefaultTownDesc)
    epc_service: EPCLookupService = dataclasses.field(default_factory=_DefaultEPCLookup)
    council_tax_service: CouncilTaxService = dataclasses.field(default_factory=_DefaultCouncilTax)
    rail_fare_service: RailFareService = dataclasses.field(default_factory=_DefaultRailFare)
    drive_time_service: DriveTimeService = dataclasses.field(default_factory=_DefaultDriveTimeService)
    oauth_service: OAuthService = dataclasses.field(default_factory=_DefaultOAuthService)
    persons_source: UserInputNode[list[Person]] = dataclasses.field(
        default_factory=lambda: _make_settings_source("persons", list[Person], make_default_persons)
    )
    # Individual financial setting nodes (created in __post_init__)
    setting_nodes: dict[str, UserInputNode] = dataclasses.field(default_factory=dict)
    # SettingsNode aggregate (lazily created, accessed via settings_view)
    _settings_view: Any | None = dataclasses.field(default=None)
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

    def __post_init__(self):
        from houses.nodes.settings_node import SETTING_DEFAULTS

        # Create individual setting nodes alongside the existing financial_source
        if not self.setting_nodes:
            self.setting_nodes = {}
            for node_id, (val_type, default_fn) in SETTING_DEFAULTS.items():
                self.setting_nodes[node_id] = _make_settings_source(node_id, val_type, default_fn)

    @property
    def settings_view(self):
        """Lazy SettingsNode aggregate for API use.

        Reads from individual setting nodes and returns the same dict
        shape as financial_source did. Created once per Services instance.
        """
        if self._settings_view is None:
            from houses.nodes.settings_node import SettingsNode

            self._settings_view = SettingsNode(
                "financial_aggregate",
                setting_nodes=self.setting_nodes,
            )
        return self._settings_view

    async def tfl_plan(self, origin: str, destination: str, label: str) -> Attempt[Commute]:
        """Plan a TfL transit route. Wraps the real client for DI."""
        from houses.tfl_client import TflClient

        return await TflClient(origin, destination, label).plan()
