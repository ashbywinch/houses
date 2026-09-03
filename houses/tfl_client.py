"""TfL API client for public-transit route planning in London."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.http_error import HttpError
from houses.api_cache import cached_async_client, evict_cached, get_cached, set_cached
from houses.car_park import ApcoaCarParkLookup, CarParkRegistry
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.location import geocode, geocode_address
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.settings import settings
from houses.stations import Station
from houses.stations import find as find_station
from houses.transit_route import apply_park_and_ride_to_journeys

logger = logging.getLogger(__name__)

# lucidlint: ignore global-state static TfL mode-name → LegMode mapping table; never mutated
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
_MODE_MAP: dict[str, LegMode] = {
    "walking": LegMode.WALK,
    "tube": LegMode.TUBE,
    "bus": LegMode.BUS,
    "national-rail": LegMode.TRAIN,
    "overground": LegMode.OVERGROUND,
    "dlr": LegMode.DLR,
    "tram": LegMode.TRAM,
    "driving": LegMode.DRIVE,
    "cycle": LegMode.CYCLE,
}
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409


def _friendly_tfl_message(status: int) -> str:
    """UI-safe reason for a TfL API failure.

    404 is TfL's deterministic "cannot route this journey" answer; 409
    is a planner outage.  Anything else degrades to a generic phrase —
    never the raw response body, which stays in HttpError.message/body.
    """
    if status == HTTP_NOT_FOUND:
        return "TfL couldn't find a route for this journey"
    if status == HTTP_CONFLICT:
        return "TfL's route planner is unavailable right now"
    return "TfL couldn't plan this route"

# lucidlint: ignore data-clump the arrival text and leg minutes travel together through every formatter by
# lucidlint: ignore data-clump (clean_arr, duration, instr) is the dispatch-table formatter signature shared by seven
def _tube_leg_label(clean_arr: str, duration: int, instr: str) -> str:  # lucidlint: ignore data-clump (duration,
    """Label for a tube leg — the line name comes from the instruction summary."""
    line_from_instr = instr.split(" to ")[0] if " to " in instr else ""
    tube_line = line_from_instr.replace(" line", "").replace(" Line", "").strip()
    return f"{tube_line} line to {clean_arr} ({duration}m)"


def _driving_leg_label(clean_arr: str, duration: int, instr: str) -> str:
    """Label for a driving leg — destination only when the leg names one."""
    return f"Drive to {clean_arr} ({duration}m)" if clean_arr else f"Drive {duration}m"


def _bus_leg_label(clean_arr: str, duration: int, instr: str) -> str:
    """Label for a bus leg — the route number comes from the instruction summary."""
    bus_num = instr.split(" bus")[0] if " bus" in instr else ""
    return f"bus({bus_num}) to {clean_arr} ({duration}m)" if bus_num else f"Bus to {clean_arr} ({duration}m)"


def _train_leg_label(clean_arr: str, duration: int, instr: str) -> str:
    """Label for a national-rail leg."""
    return f"Train to {clean_arr} ({duration}m)"


def _overground_leg_label(clean_arr: str, duration: int, instr: str) -> str:
    """Label for an overground leg."""
    return f"Overground to {clean_arr} ({duration}m)"


def _dlr_leg_label(clean_arr: str, duration: int, instr: str) -> str:
    """Label for a DLR leg."""
    return f"DLR to {clean_arr} ({duration}m)"


def _tram_leg_label(clean_arr: str, duration: int, instr: str) -> str:
    """Label for a tram leg."""
    return f"Tram to {clean_arr} ({duration}m)"


# lucidlint: ignore record-shape static dispatch table mode → formatter callable; not a data record
# lucidlint: ignore global-state static TfL mode-name → leg-label formatter table; never mutated
_LEG_LABEL_FORMATTERS: dict[str, Callable[[str, int, str], str]] = {
    "tube": _tube_leg_label,
    "driving": _driving_leg_label,
    "bus": _bus_leg_label,
    "national-rail": _train_leg_label,
    "overground": _overground_leg_label,
    "dlr": _dlr_leg_label,
    "tram": _tram_leg_label,
}

@dataclass(frozen=True)
class JourneySummary:
    """(duration_min, cost, route_summary) verdict from a TfL journey set —
    named so call sites and tests read the fields by meaning, not position."""

    duration: int | None
    cost: float | None
    route_summary: str



@dataclass(frozen=True)
class TflRouteOptions:
    """Routing policy for one TflClient: park-and-ride, bus mode, and the
    DI seams (cached call wrapper, plan override)."""

    park_and_ride: bool = False
    allow_bus: bool = False
    cached_call: Callable | None = None
    plan_override: Callable | None = None



@dataclass(frozen=True)
class ParkingCostResult:
    """(parking_cost, new_daily_cost, cost_groups) from _add_parking_cost —
    named so the call site reads the fields by meaning, not position."""

    parking_cost: Money | None
    new_cost: Money | None
    cost_groups: list[CostGroup]


@dataclass(frozen=True)
class _CacheEnvelope:
    """The wrapped-error envelope stored in the fare cache file: a
    deterministic non-2xx TfL response with its status preserved."""

    status: int
    body: object

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the cache-file shape (coding-standards)
        return dict(_cached_status=self.status, _cached_body=self.body)


class TflClient:
    """TfL API client for public-transit route planning in London.

    Usage::

        route = TflClient(
            origin="GU21 7QF",
            destination="SW1V 2QQ",
            label="Simon \u2014 Pimlico / Victoria",
            options=TflRouteOptions(park_and_ride=True),
        )
        commute: Attempt[Commute] = await route.plan()
    """

    TFL_JOURNEY_URL = "https://api.tfl.gov.uk/Journey/JourneyResults"
    FALLBACK_TUBE_SINGLE_GBP = "2.80"

    def __init__(
        self,
        origin_postcode: str,
        destination_postcode: str,
        label: str,
        options: TflRouteOptions | None = None,
    ):
        self._origin: str = origin_postcode
        self._destination: str = destination_postcode
        self._label: str = label
        opts = options or TflRouteOptions()
        self._park_and_ride: bool = opts.park_and_ride
        self._allow_bus: bool = opts.allow_bus
        self._no_route_reason: str = ""
        self._no_route_detail: str = ""
        # DI seams (docs/testing-standards: no monkeypatch in new tests).
        self._cached_call: Callable = opts.cached_call or TflClient._cached_api_call
        self._plan_override: Callable | None = opts.plan_override

    # ── Public API ──────────────────────────────────────────────────

    async def plan(self) -> Attempt[Commute]:
        """Fetch TfL route, enrich with costs, and return a Commute."""
        if self._plan_override is not None:
            return await self._plan_override(self)
        data = await self._fetch_data()
        if data is not None and self._park_and_ride:
            data = await apply_park_and_ride_to_journeys(
                data, self._origin, int(settings.max_walk_to_station.magnitude)
            )
        return await self._process_data(data)

    @staticmethod
    async def route_duration(
        origin: str,
        destination_postcode: str,
        *,
        allow_bus: bool = True,
        fetch: Callable[[str, dict], Awaitable[dict | None]] | None = None,
    ) -> int | None:
        """Route one origin → postcode pair and return the fastest duration in minutes.

        Public entry point for external tooling (``tools/commute/station_shed.py``)
        that needs durations, not Commute enrichment. Builds the same request as
        ``plan()`` (nationalSearch, arriving 09:00 weekday, disk-cached, retry with
        backoff on transient errors and network failures). Returns ``None`` when
        TfL cannot route or every attempt fails — never raises for a missing route.

        ``origin`` is any TfL origin identifier: ``"lat,lon"``, a place name, or a
        stop id. ``fetch`` is injectable for tests (default: the cached/retry
        wrapper; transient error responses are never cached, so retries are
        genuine).
        """
        modes = ["tube", "overground", "dlr", "tram", "national-rail", "walking"]
        if allow_bus:
            modes.append("bus")
        url = f"{TflClient.TFL_JOURNEY_URL}/{origin}/to/{destination_postcode}"
        params = {
            "nationalSearch": "true",
            "timeIs": "arriving",
            "journeyPreference": "leasttime",
            "mode": ",".join(modes),
            **TflClient._next_weekday_date_params(),
            **TflClient._tfl_auth_params(),
        }
        fetch = fetch or TflClient._cached_with_retry
        data = await fetch(url, params)
# lucidlint: ignore special-case sentinel handling is the contract here
        if data is None:
            return None
        # A 300 disambiguation means the name matched multiple places (usually
        # streets). The exact station is among the options as a national-rail
        # StopPoint — route from its id instead of giving up (observed:
        # Haywards Heath, Tring, Burgess Hill all fail both origin forms).
        station_id = TflClient._disambiguate_national_rail(data)
        if station_id is not None:
            url2 = f"{TflClient.TFL_JOURNEY_URL}/{station_id}/to/{destination_postcode}"
            data2 = await fetch(url2, params)
            if data2 is not None:
                data = data2
        duration = TflClient._pick_best_journey(data).duration
        return duration

    @staticmethod
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    def _disambiguate_national_rail(data: dict) -> str | None:
        """Extract the national-rail StopPoint id from a 300 disambiguation body.

        Returns the option's ``parameterValue`` (a routable stop id) for the
        first StopPoint whose modes include national-rail, or ``None`` when the
        body is not a disambiguation or has no such option.
        """
        disamb = data.get("fromLocationDisambiguation", {})
        for option in disamb.get("disambiguationOptions", []):
            place = option.get("place", {})
            if place.get("placeType") == "StopPoint" and "national-rail" in (place.get("modes") or []):
                return option.get("parameterValue")
        return None
    # ── TfL helper functions ─────────────────────────────────────────

    @staticmethod
    async def get_tube_leg_fare(
        from_station: Station,
        to_postcode: str,
        _data: dict | None = None,
        *,
        _client_factory: Callable | None = None,
    ) -> Money | None:
        """Get the peak single fare for a tube journey between a station and a postcode.

        Uses the TfL Journey API with a weekday morning peak departure time (09:00,
        which is within the zone 1 peak window of 06:30\u201309:30 on weekdays).
        Returns a ``Money`` single fare, or ``None`` if TfL can't route the
        journey (walking distance from the NR terminus to the destination)
        or if the API call fails.  A deterministic no-route 404 converts to
        ``None`` (the caller applies its fallback fare) — out-of-area
        destinations legitimately 404 here; transient errors (429/5xx)
        still raise for DAG retry.  ``_client_factory`` is injectable for tests.
        """
        if _data is not None:
            return TflClient._parse_tube_fare(_data)
        url = f"{TflClient.TFL_JOURNEY_URL}/{from_station.name}/to/{to_postcode}"
        params = TflClient._next_weekday_date_params()
        params["nationalSearch"] = "false"
        params.update(TflClient._tfl_auth_params())

        try:
            data = await TflClient._cached_api_call(url, params, _client_factory=_client_factory)
        except HttpError as e:
            if e.status != 404:
                raise
            return None
        if data is None:
            return None
        return TflClient._parse_tube_fare(data)

    @staticmethod
    def _parse_tube_fare(data: dict) -> Money | None:
        """Extract the peak single fare from a TfL journey response.

        TfL returns ``totalCost`` in pence (integer).  Divides by 100 to
        get pounds, letting Money/Decimal handle the conversion.
        """
        journeys = data.get("journeys", [])
        if not journeys:
            return None
        best = min(journeys, key=lambda j: j.get("duration", 9999))
        fare = best.get("fare", {})
        if fare and fare.get("totalCost") is not None:
            return Money(str(fare["totalCost"] / 100), "GBP")
        return None

    @staticmethod
    def _next_weekday_date_params() -> dict[str, str]:
        """Return ``date`` and ``time`` params for the next upcoming weekday at 09:00 local time."""
        # Work in local time since TfL API expects local dates
        now_local = datetime.now(UTC).astimezone()
        if now_local.weekday() < 5 and now_local.hour < 9:
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            return {"date": now_local.strftime("%Y%m%d"), "time": "0900"}
        target = now_local + timedelta(days=1)
        while target.weekday() >= 5:
            target += timedelta(days=1)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        return {"date": target.strftime("%Y%m%d"), "time": "0900"}

    @staticmethod
    def _tfl_auth_params() -> dict[str, str]:
        params = {}
        if settings.tfl_api_key:
            params["app_key"] = settings.tfl_api_key
        return params

    @staticmethod
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    def _format_route_summary(journey: dict) -> str:
        legs = journey.get("legs", [])
        parts: list[str] = []

        for i, leg in enumerate(legs):
            mode = leg.get("mode", {}).get("name", "?")
            duration = leg.get("duration", 0)
            instr = leg.get("instruction", {}).get("summary", "")
            arr = leg.get("arrivalPoint", {}).get("commonName", "")
            is_last = i == len(legs) - 1

            if mode == "walking":
                if is_last:
                    parts.append(f"walk {duration}m")
                else:
                    clean_arr = Station.short_name(arr) if arr else ""
                    is_station = bool(arr) and Station.short_name(arr) != arr
                    if is_station and clean_arr:
                        parts.append(f"walk to {clean_arr} ({duration}m)")
                    else:
                        parts.append(f"walk {duration}m")
                continue

            clean_arr = Station.short_name(arr) if arr else ""

            formatter = _LEG_LABEL_FORMATTERS.get(mode)
            if formatter is None:
                label = f"{mode} to {clean_arr} ({duration}m)" if clean_arr else f"{mode} {duration}m"
            else:
                label = formatter(clean_arr, duration, instr)

            parts.append(label)

        return " \u2192 ".join(parts)

    @staticmethod
    def _pick_best_journey(data: dict | None) -> JourneySummary:
        if data is None:
            return JourneySummary(None, None, "")
        journeys = data.get("journeys", [])
        if not journeys:
            logger.debug("_pick_best_journey: no journeys in response")
            return JourneySummary(None, None, "")
        best = min(journeys, key=lambda j: j.get("duration", 9999))
        duration = best.get("duration")
        first_leg = (best.get("legs") or [{}])[0]
        logger.debug(
            "_pick_best_journey: %d journeys, best=%dm, first_leg=%s '%s'",
            len(journeys),
            duration,
            first_leg.get("mode", {}).get("name", "?"),
            first_leg.get("arrivalPoint", {}).get("commonName", ""),
        )
        fare = best.get("fare")
        cost = None
        if fare and fare.get("totalCost") is not None:
            cost = round(fare["totalCost"] / 100.0 * 2, 2)
        route_summary = TflClient._format_route_summary(best)
        return JourneySummary(duration, cost, route_summary)

    @staticmethod
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
# lucidlint: ignore record-shape homogeneous (leg, mode-name) parse pairs — a keyed collection, not a field-wise record
    def _parse_tfl_legs(tfl_legs: list[dict]) -> list[tuple[JourneyLeg, str]]:
        """Parse TfL API legs into (JourneyLeg, mode_name) pairs.

        Every leg returned has ``start_station``, ``end_station``,
        ``line_name``, ``duration_minutes``, and ``mode`` set from the
        TfL response fields.
        """
        result: list[tuple[JourneyLeg, str]] = []
        for leg in tfl_legs:
            mode_name = leg.get("mode", {}).get("name", "?")
            duration = int(leg.get("duration", "0"))
            leg_mode = _MODE_MAP.get(mode_name, LegMode.WALK)
            dep_station = leg.get("departurePoint", {}).get("commonName", "")
            arr_station = leg.get("arrivalPoint", {}).get("commonName", "")
            line_name = leg.get("route", {}).get("name", "")
            instr = leg.get("instruction", {}).get("summary", "")

            # Fallback: extract line name from TfL instruction text when
            # ``route.name`` is empty (some tube responses omit it).
            if not line_name and mode_name == "tube" and instr:
                line_from_instr = instr.split(" to ")[0].replace(" line", "").replace(" Line", "").strip()
                if line_from_instr:
                    line_name = line_from_instr

            jl = JourneyLeg(
                mode=leg_mode,
                duration=Quantity(duration, "minute"),
                start_station=dep_station,
                end_station=arr_station,
                line_name=line_name,
            )
            result.append((jl, mode_name))
        return result

    # ── Internal fetch / process ─────────────────────────────────────

    @staticmethod
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def _cached_with_retry(
        url: str, params: dict, *, attempts: int = 3, base_delay: float = 1.0, fetch=None
    ) -> dict | None:
        """Cached TfL call with backoff on transient HTTP errors and network failures.

        Retries are genuine: ``_cached_api_call`` never caches error responses,
        so a failed attempt cannot short-circuit a later one via the disk cache.
        ``fetch`` is injectable for tests (default: the cached API call).
        """
        fetch = fetch or TflClient._cached_api_call
        for attempt in range(attempts):
            try:
                return await fetch(url, params)
            except HttpError as e:
                if e.is_rate_limit() or e.is_server_error():
                    delay = base_delay * (2**attempt)
                    logger.warning("TfL transient %s for %s — retry in %.1fs", e.status, url, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.warning("TfL client error %s for %s — station excluded", e.status, url)
                return None
            except httpx.RequestError as e:
                delay = base_delay * (2**attempt)
                logger.warning("TfL network error for %s (%s) — retry in %.1fs", url, e.__class__.__name__, delay)
                await asyncio.sleep(delay)
                continue
        logger.error("TfL transient errors exhausted for %s — station excluded", url)
        return None

    @staticmethod
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    def _is_transient_error_body(data: dict) -> bool:
        """True for cached entries that are TRANSIENT error responses.

        Only 429/5xx must be rejected and evicted on the cache hit path — a
        transient outage must not poison the route, and retries must be
        genuine. Deterministic no-route responses (404, "cannot route" bodies)
        are legitimately cached and served: re-hitting the endpoint for the
        same impossible request wastes calls. Two legacy shapes exist: the
        transport's ``{"_cached_status": ...}`` wrapper and TfL's raw
        ``ApiError`` JSON (status in ``httpStatusCode``).
        """
        for key in ("_cached_status", "httpStatusCode"):
            status = data.get(key)
            if isinstance(status, int):
                # Poison: auth failures (401/403), planner outages (409), rate
                # limits (429) and server errors are transient — they must not
                # be served from cache. Only genuine no-route (404) bodies are
                # deterministic.
                return status in (401, 403, 409, 429) or status >= 500
        return False

    @staticmethod
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def _cached_api_call(
        url: str, params: dict, *, _client_factory: Callable | None = None
    ) -> dict | None:
        """Make a cached TfL API call. Strips auth from cache keys.
        Transient errors (429, 5xx, httpx.RequestError) are re-raised for DAG retry.
        Deterministic responses (2xx/3xx/4xx, including 404 "cannot route"
        bodies) are cached and served — re-hitting the endpoint for the same
        impossible request wastes calls. 429/5xx are never cached, so retries
        stay genuine.
        Unexpected response format errors (KeyError, IndexError, TypeError) are
        NOT caught — they propagate so the operator knows the API format changed.
        ``_client_factory`` is injectable for tests (resolved at call time so
        monkeypatching ``cached_async_client`` keeps working)."""
        cache_params = {k: v for k, v in params.items() if k != "app_key"}
        cached = get_cached("GET", url, cache_params)
        if cached is not None:
            if isinstance(cached, dict) and "_cached_status" in cached and "_cached_body" in cached:
                # Wrapped deterministic non-2xx — a cached 404 must behave
                # like a LIVE 404 (raise HttpError) so the caller's
                # conversion sets the no-route reason/detail; other
                # statuses unwrap as before.  A WRAPPED transient status
                # (legacy 429/5xx) is evicted, never served as data.
                if cached["_cached_status"] == 404:
                    body = cached["_cached_body"]
                    # Same two-tier shape as the LIVE 404: raw body in
                    # message/body, friendly text to the UI.
                    raise HttpError(
                        404,
                        message=str(body)[:200],
                        body=body,
                        user_message=_friendly_tfl_message(404),
                    )
                if TflClient._is_transient_error_body(cached):
                    logger.warning("evicting cached transient error response for %s", url)
                    evict_cached("GET", url, cache_params, None)
                    cached = None
                else:
                    return cached["_cached_body"]
            elif TflClient._is_transient_error_body(cached):
                # Legacy poisoned entry (429/5xx cached before the rule): reject
                # and evict so the route is re-fetched — transient errors are
                # never served from cache.
                # lucidlint: ignore duplicate-block the legacy-entry eviction intentionally mirrors the
                logger.warning("evicting cached transient error response for %s", url)
                evict_cached("GET", url, cache_params, None)
                cached = None
            else:
                return cached
        async with (_client_factory or cached_async_client)(timeout=20.0) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            # Cache deterministic responses — 2xx/3xx/4xx (including 404
            # "cannot route this station" bodies: re-hitting the endpoint for
            # the same impossible request wastes calls). Non-2xx are wrapped as
            # {"_cached_status", "_cached_body"} so the status survives cache
            # hits. NEVER cache transient errors (429, 5xx): a cached outage
            # body would poison the route and make retries non-genuine.
            if resp.status_code < 300:
                set_cached("GET", url, cache_params, None, data)
            elif 300 <= resp.status_code < 400:
                set_cached(
                    "GET", url, cache_params, None,
                    _CacheEnvelope(status=resp.status_code, body=data).to_dict(),
                )
            elif resp.status_code == 404:
                # 404 "cannot route this station" is genuinely deterministic —
                # re-hitting the endpoint for the same impossible request
                # wastes calls. Every OTHER 4xx is transient-ish (401/403 key
                # expiry, 409 planner outage) and must not poison the cache.
                set_cached(
                    "GET", url, cache_params, None,
                    _CacheEnvelope(status=404, body=data).to_dict(),
                )
            if resp.status_code == 429 or (500 <= resp.status_code < 600):
                raise HttpError(
                    resp.status_code,
                    body=str(data),
                    user_message="TfL is busy right now — try again shortly",
                )
            if 400 <= resp.status_code < 500:
                # Non-transient client error (e.g. 404 no route, 409 route
                # planner unavailable) — keep the raw body in the internal
                # message (logs) and `body`; the UI sees only the friendly
                # user_message (walkthrough run 3 — a raw TfL 404 blob was
                # rendered to the user).
                reason = str(data)[:200]
                raise HttpError(
                    resp.status_code,
                    body=str(data),
                    message=reason,
                    user_message=_friendly_tfl_message(resp.status_code),
                )
            return data
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def _fetch_data(self) -> dict | None:
        """Call TfL API and return the raw JSON response, or None on failure.

        A 404 "No journey found for your inputs" is TfL's deterministic
        no-route answer (the only route may need a mode we excluded, e.g.
        Wycombe → Bracknell needs bus) — it returns None so ``plan()``
        yields a succeeded-infeasible commute and the selector falls back
        to with-bus / drive / walk instead of failing hard.  Other
        HttpErrors (429/5xx transient, 409 outage, 401/403 auth) still
        propagate so the DAG retries or surfaces them.
        """
        modes = ["tube", "overground", "dlr", "tram", "national-rail", "walking"]
        if self._allow_bus:
            modes.append("bus")

        url = f"{TflClient.TFL_JOURNEY_URL}/{self._origin}/to/{self._destination}"
        params = {
            "nationalSearch": "true",
            "timeIs": "arriving",
            "journeyPreference": "leasttime",
            "mode": ",".join(modes),
            **TflClient._next_weekday_date_params(),
            **TflClient._tfl_auth_params(),
        }

        try:
            data = await self._cached_call(url, params)
            if data is not None and "Disambiguation" in str(data.get("$type", "")):
                data = await self._geocode_fallback(params)
        except HttpError as e:
            if e.status != 404:
                raise
            mode_note = "" if self._allow_bus else "bus mode excluded"
            # User-facing reason stays clean (two-tier messaging: no
            # status codes or probe strategy in UI text); the internal
            # detail rides separately to the node provenance.
            self._no_route_reason = "TfL couldn't find a route for this journey"
            self._no_route_detail = f"HTTP 404{mode_note and ', ' + mode_note}"
            return None
        return data

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def _process_data(self, data: dict | None) -> Attempt[Commute]:
        """Turn raw TfL API data into a Commute.  Pure logic — no HTTP.

        Every cost lives on a CostGroup in the result's details. The total
        daily_cost is derived by summing all CostGroup costs.
        """
        duration_minutes: int | None = None
        cost_groups: list[CostGroup] = []

        if data is not None:
            duration_minutes = TflClient._pick_best_journey(data).duration
            cost_groups = self._build_cost_groups(data)

        # Parking cost — adds a CostGroup with the parking fee
        if self._park_and_ride and duration_minutes is not None and data is not None:
            parking = await self._add_parking_cost(data, None)
            cost_groups.extend(parking.cost_groups)

        # Derive total from CostGroup costs
        total = Money(amount="0", currency="GBP")
        for cg in cost_groups:
            if cg.cost is not None:
                if not isinstance(cg.cost, Money):
                    raise TypeError(f"CostGroup.cost must be Money or None, got {type(cg.cost).__name__}: {cg.cost}")
                total += cg.cost
        daily_cost_gbp = total

        result = Commute(
            person=Person(name="", has_car=False),
            label=self._label,
            destination=PlaceOfInterest(label=self._label, address=self._destination),
            duration=Quantity(duration_minutes, "minute") if duration_minutes is not None else None,
            daily_cost=daily_cost_gbp,
            mode="transit",
            _details=tuple(cost_groups),
        )
        if duration_minutes is not None:
            return Attempt.succeeded(result)
        # A valid TfL response with no journey is a successful "no transit
        # route" answer — succeeded-infeasible so the commute selector can
        # fall back to drive/walk. Genuine API failures raise HttpError in
        # _cached_api_call and surface as impossible with the real reason.
        return Attempt.succeeded(
            Commute(
                person=Person(name="", has_car=False),
                label=self._label,
                destination=PlaceOfInterest(label=self._label, address=self._destination),
                duration=Quantity(0, "minute"),  # type: ignore[arg-type]  # pint's stub types Quantity(0, "minute") as PlainQuantity, which basedpyright won't assign to the field's bare Quantity[Unknown] (pint's generic is invariant); at runtime PlainQuantity IS a pint Quantity and valid here
                daily_cost=Money(amount="0", currency="GBP"),
                mode="transit",
                _details=(),
                infeasible=True,
                no_route_reason=self._no_route_reason or "TfL couldn't find a route for this journey",
            )
        )

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def _geocode_fallback(self, params: dict) -> dict | None:
        """Handle TfL 300 response by geocoding the origin and retrying."""
        pc_match = re.search(r"[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?", self._origin)
        pc = pc_match.group(0).strip().upper() if pc_match else None
        coords = (await geocode_address(self._origin)).value_or_none()
        if coords is None and pc:
            coords = (await geocode(pc)).value_or_none()
        if coords:
            url2 = f"{TflClient.TFL_JOURNEY_URL}/{coords.lat},{coords.lon}/to/{self._destination}"
            d2 = await TflClient._cached_api_call(url2, params)
            if d2 is not None:
                return d2
        return None

    @staticmethod
    async def _add_parking_cost(
        data: dict,
        current_cost: Money | None = None,
        _registry: CarParkRegistry | None = None,
    ) -> ParkingCostResult:
        """Look up parking costs when park-and-ride used a driving leg.

        Returns a ``ParkingCostResult`` whose ``cost_groups`` contains a
        single ``CostGroup`` with the parking fee (operator ``"ParkCo"``)
        so that the resulting commute's daily cost reflects parking.

        ``_registry`` \u2014 optional ``CarParkRegistry`` instance.  When
        omitted (production), a default registry loaded from CSV is used.
        Tests pass a pre-populated registry via ``from_car_parks()``.
        """
        journeys = data.get("journeys", [])
        if not journeys:
            return ParkingCostResult(None, current_cost, [])
        best = min(journeys, key=lambda j: j.get("duration", 9999))
        legs = best.get("legs", [])
        if not legs or legs[0].get("mode", {}).get("name") != "driving":
            return ParkingCostResult(None, current_cost, [])

        station_name = legs[0].get("arrivalPoint", {}).get("commonName", "")
        if not station_name:
            return ParkingCostResult(None, current_cost, [])

        station = find_station(station_name)
        if station is None:
            return ParkingCostResult(None, current_cost, [])

        parking = _registry or CarParkRegistry()
        car_park = parking.find_car_park(station)

        if car_park is None:
            result = await ApcoaCarParkLookup(parking).add_nearest_car_park_for(station)
            car_park = result.value_or_none() if result.succeeded else None
        elif car_park.daily_cost is None:
            result = await ApcoaCarParkLookup(parking).load_costs(car_park, station)
            if result.succeeded:
                car_park = result.value_or_none()

        if car_park is None or car_park.daily_cost is None:
            return ParkingCostResult(None, current_cost, [])

        parking_cost = car_park.daily_cost
        new_cost = current_cost + parking_cost if current_cost is not None else parking_cost

        parking_group = CostGroup(
            legs=(JourneyLeg(mode=LegMode.PARK, duration=Quantity(0, "minute")),),
            operator=f"ParkCo: {car_park.name}",
            cost=parking_cost,
        )
        return ParkingCostResult(parking_cost, new_cost, [parking_group])

    @staticmethod
    def _build_cost_groups(data: dict) -> list[CostGroup]:
        """Parse TfL response legs into CostGroup objects.

        Walking legs before/after transit and between transit lines
        are boring (no cost).  Transit legs are grouped by the fare
        structure in the API response — if ``fare.fares`` provides
        separate costs per mode, each mode gets its own CostGroup.

        The TfL fare MUST land on the transit groups: the total
        daily_cost is derived by summing CostGroup costs, and a £0
        transit route wrongly triggers the National Rail fare node
        (which then kills commutes whose rail leg has no NR fare, e.g.
        Elizabeth-line legs — "no fare STL→EAL").
        """
        journeys = data.get("journeys", [])
        if not journeys:
            return []
        best = min(journeys, key=lambda j: j.get("duration", 9999))
        # lucidlint: ignore duplicate-block the no-legs guard intentionally mirrors the no-journeys guard above —
        tfl_legs = best.get("legs", [])
        if not tfl_legs:
            return []

        fare = best.get("fare", {})
        fare_fares: list[dict] = fare.get("fares", []) if fare else []
        # Per-mode single fares in pence (e.g. national-rail + tube).
        mode_single_pence: dict[str, int] = {
            f["mode"]: int(f["cost"]) for f in fare_fares if f.get("cost") is not None and f.get("mode")
        }
        # Whole-journey single fare in pence — the fallback when the API
        # gives one totalCost instead of per-mode fares.
        total_single_pence = fare.get("totalCost") if fare else None

        builder = _CostGroupBuilder(mode_single_pence, total_single_pence)
        for jl, mode_name in TflClient._parse_tfl_legs(tfl_legs):
            if mode_name == "walking":
                builder.add_walking(jl)
            else:
                builder.add_transit(jl, mode_name)
        return builder.build()


class _CostGroupBuilder:
    """Stateful accumulator that groups parsed TfL legs into CostGroups.

    Walking legs before/after transit become standalone no-cost groups;
    walking legs *inside* a transit run merge into the transit group.
    Transit legs are flushed per mode when the fare structure has
    per-mode costs, carrying the mode's fare (×2 for the return trip);
    a whole-journey ``totalCost`` lands on the first transit group so
    the sum of group costs equals the total exactly.
    """

    def __init__(self, mode_single_pence: dict[str, int], total_single_pence: int | None) -> None:
        self._mode_single_pence: dict[str, int] = mode_single_pence
        self._total_single_pence: int | None = total_single_pence
        self._groups: list[CostGroup] = []
        self._current_legs: list[JourneyLeg] = []
        self._current_mode: str | None = None
        self._in_transit: bool = False
        self._total_applied: bool = False

    def add_walking(self, jl: JourneyLeg) -> None:
        if self._in_transit:
            self._current_legs.append(jl)
            return
        self._groups.append(CostGroup(legs=(jl,)))

    def add_transit(self, jl: JourneyLeg, mode_name: str) -> None:
        # Transit leg — check if we need a new CostGroup
        if (
            self._current_mode is not None
            and self._current_mode != mode_name
            and (self._mode_single_pence or self._in_transit)
        ):
            self._flush_transit()

        if not self._in_transit and self._current_legs:
            self._groups.append(CostGroup(legs=tuple(self._current_legs)))
            self._current_legs = []
        self._in_transit = True
        self._current_mode = mode_name
        self._current_legs.append(jl)

    def _flush_transit(self) -> None:
        """Emit the accumulated transit legs as a CostGroup carrying
        its TfL fare (per-mode cost × 2 for the return trip)."""
        if not self._current_legs:
            return
        cost: Money | None = None
        if self._current_mode is not None and self._current_mode in self._mode_single_pence:
            cost = Money(str(round(self._mode_single_pence[self._current_mode] / 100.0 * 2, 2)), "GBP")
        elif self._total_single_pence is not None and not self._total_applied:
            # No per-mode split — the whole-journey fare goes on the
            # FIRST transit group so the sum equals the total exactly.
            cost = Money(str(round(self._total_single_pence / 100.0 * 2, 2)), "GBP")
            self._total_applied = True
        self._groups.append(
            CostGroup(
                legs=tuple(self._current_legs),
                operator="TfL",
                cost=cost,
            )
        )
        self._current_legs = []
        self._current_mode = None

    def build(self) -> list[CostGroup]:
        self._flush_transit()
        return self._groups
