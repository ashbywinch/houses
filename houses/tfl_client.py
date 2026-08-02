"""TfL API client for public-transit route planning in London."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.http_error import HttpError
from houses.api_cache import cached_async_client, evict_cached, get_cached, set_cached
from houses.car_park import CarParkRegistry
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.config import settings
from houses.location import _geocode_address, geocode
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.stations import Station
from houses.stations import find as find_station
from houses.transit_route import _apply_park_and_ride_to_journeys

logger = logging.getLogger(__name__)

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


class TflClient:
    """TfL API client for public-transit route planning in London.

    Usage::

        route = TflClient(
            origin="GU21 7QF",
            destination="SW1V 2QQ",
            label="Simon \u2014 Pimlico / Victoria",
            park_and_ride=True,
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
        park_and_ride: bool = False,
        allow_bus: bool = False,
    ):
        self._origin = origin_postcode
        self._destination = destination_postcode
        self._label = label
        self._park_and_ride = park_and_ride
        self._allow_bus = allow_bus

    # ── Public API ──────────────────────────────────────────────────

    async def plan(self) -> Attempt[Commute]:
        """Fetch TfL route, enrich with costs, and return a Commute."""
        data = await self._fetch_data()
        if data is not None and self._park_and_ride:
            data = await _apply_park_and_ride_to_journeys(
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
        wrapper; error responses are never cached, so retries are genuine).
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
        if data is None:
            return None
        duration, _, _ = TflClient._pick_best_journey(data)
        return duration
    # ── TfL helper functions ─────────────────────────────────────────

    @staticmethod
    async def get_tube_leg_fare(
        from_station: Station,
        to_postcode: str,
        _data: dict | None = None,
    ) -> Money | None:
        """Get the peak single fare for a tube journey between a station and a postcode.

        Uses the TfL Journey API with a weekday morning peak departure time (09:00,
        which is within the zone 1 peak window of 06:30\u201309:30 on weekdays).
        Returns a ``Money`` single fare, or ``None`` if TfL can't route the
        journey (walking distance from the NR terminus to the destination)
        or if the API call fails.
        """
        if _data is not None:
            return TflClient._parse_tube_fare(_data)
        url = f"{TflClient.TFL_JOURNEY_URL}/{from_station.name}/to/{to_postcode}"
        params = TflClient._next_weekday_date_params()
        params["nationalSearch"] = "false"
        params.update(TflClient._tfl_auth_params())

        data = await TflClient._cached_api_call(url, params)
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
            return {"date": now_local.strftime("%Y%m%d"), "time": "0900"}
        target = now_local + timedelta(days=1)
        while target.weekday() >= 5:
            target += timedelta(days=1)
        return {"date": target.strftime("%Y%m%d"), "time": "0900"}

    @staticmethod
    def _tfl_auth_params() -> dict[str, str]:
        params = {}
        if settings.tfl_api_key:
            params["app_key"] = settings.tfl_api_key
        return params

    @staticmethod
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

            if mode == "tube":
                line_from_instr = instr.split(" to ")[0] if " to " in instr else ""
                tube_line = line_from_instr.replace(" line", "").replace(" Line", "").strip()
                label = f"{tube_line} line to {clean_arr} ({duration}m)"
            elif mode == "driving":
                label = f"Drive to {clean_arr} ({duration}m)" if clean_arr else f"Drive {duration}m"
            elif mode == "bus":
                bus_num = instr.split(" bus")[0] if " bus" in instr else ""
                label = (
                    f"bus({bus_num}) to {clean_arr} ({duration}m)" if bus_num else f"Bus to {clean_arr} ({duration}m)"
                )
            elif mode == "national-rail":
                label = f"Train to {clean_arr} ({duration}m)"
            elif mode == "overground":
                label = f"Overground to {clean_arr} ({duration}m)"
            elif mode == "dlr":
                label = f"DLR to {clean_arr} ({duration}m)"
            elif mode == "tram":
                label = f"Tram to {clean_arr} ({duration}m)"
            else:
                label = f"{mode} to {clean_arr} ({duration}m)" if clean_arr else f"{mode} {duration}m"

            parts.append(label)

        return " \u2192 ".join(parts)

    @staticmethod
    def _pick_best_journey(data: dict | None) -> tuple[int | None, float | None, str]:
        if data is None:
            return None, None, ""
        journeys = data.get("journeys", [])
        if not journeys:
            logger.debug("_pick_best_journey: no journeys in response")
            return None, None, ""
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
        return duration, cost, route_summary

    @staticmethod
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
        logger.error("TfL transient errors exhausted for %s — station excluded", url)
        return None

    @staticmethod
    def _is_error_body(data: dict) -> bool:
        """True for cached entries that are error responses, not route data.

        Two legacy shapes were written before error caching stopped: the
        transport's ``{"_cached_status": >=400, ...}`` wrapper and TfL's raw
        ``ApiError`` JSON body. Both must be rejected on the cache hit path.
        """
        status = data.get("_cached_status")
        if isinstance(status, int) and status >= 400:
            return True
        return "$type" in data and "ApiError" in str(data["$type"])

    @staticmethod
    async def _cached_api_call(
        url: str, params: dict, *, _client_factory: Callable | None = None
    ) -> dict | None:
        """Make a cached TfL API call. Strips auth from cache keys.
        Transient errors (429, 5xx, httpx.RequestError) are re-raised for DAG retry.
        For all other statuses (including 300 disambiguation), the response body
        is cached under the original URL and returned.
        Unexpected response format errors (KeyError, IndexError, TypeError) are
        NOT caught — they propagate so the operator knows the API format changed.
        ``_client_factory`` is injectable for tests (resolved at call time so
        monkeypatching ``cached_async_client`` keeps working)."""
        cache_params = {k: v for k, v in params.items() if k != "app_key"}
        cached = get_cached("GET", url, cache_params)
        if cached is not None:
            if TflClient._is_error_body(cached):
                # Poisoned entry written before error responses stopped being
                # cached (raw ApiError JSON or a _cached_status wrapper): reject
                # and evict so the route is re-fetched instead of serving the
                # stale failure forever.
                logger.warning("evicting cached error response for %s", url)
                evict_cached("GET", url, cache_params, None)
                cached = None
            else:
                return cached
        async with (_client_factory or cached_async_client)(timeout=20.0) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            # Cache ONLY non-error responses (2xx/3xx — the 300 disambiguation
            # body is a legitimate response the geocode fallback consumes).
            # Error bodies are never cached: a transient 429/5xx must not
            # poison the route permanently, and retries stay genuine.
            if resp.status_code < 400:
                set_cached("GET", url, cache_params, None, data)
            if resp.status_code == 429 or (500 <= resp.status_code < 600):
                raise HttpError(resp.status_code, body=str(data))
            if 400 <= resp.status_code < 500:
                # Non-transient client error (e.g. 409 route planner
                # unavailable) — surface the reason instead of letting
                # _process_data reduce it to a generic "could not route".
                reason = str(data)[:200]
                raise HttpError(resp.status_code, body=str(data), message=reason)
            return data

    async def _fetch_data(self) -> dict | None:
        """Call TfL API and return the raw JSON response, or None on failure."""
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

        data = await TflClient._cached_api_call(url, params)
        if data is not None and "Disambiguation" in str(data.get("$type", "")):
            data = await self._geocode_fallback(params)
        return data

    async def _process_data(self, data: dict | None) -> Attempt[Commute]:
        """Turn raw TfL API data into a Commute.  Pure logic — no HTTP.

        Every cost lives on a CostGroup in the result's details. The total
        daily_cost is derived by summing all CostGroup costs.
        """
        duration_minutes: int | None = None
        cost_groups: list[CostGroup] = []

        if data is not None:
            dur, raw_cost, _ = TflClient._pick_best_journey(data)
            duration_minutes = dur
            cost_groups = self._build_cost_groups(data)

        # Parking cost — adds a CostGroup with the parking fee
        if self._park_and_ride and duration_minutes is not None and data is not None:
            _, _, parking_groups = await self._add_parking_cost(data, None)
            cost_groups.extend(parking_groups)

        # Derive total from CostGroup costs
        total = Money("0", "GBP")
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
                duration=Quantity(0, "minute"),  # type: ignore[arg-type]
                daily_cost=Money("0", "GBP"),
                mode="transit",
                _details=(),
                infeasible=True,
            )
        )

    async def _geocode_fallback(self, params: dict) -> dict | None:
        """Handle TfL 300 response by geocoding the origin and retrying."""
        pc_match = re.search(r"[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?", self._origin)
        pc = pc_match.group(0).strip().upper() if pc_match else None
        coords = (await _geocode_address(self._origin)).value_or_none()
        if coords is None and pc:
            coords = (await geocode(pc)).value_or_none()
        if coords:
            url2 = f"{TflClient.TFL_JOURNEY_URL}/{coords.lat},{coords.lon}/to/{self._destination}"
            d2 = await TflClient._cached_api_call(url2, params)
            if d2 is not None:
                return d2
        return None

    async def _add_parking_cost(
        self,
        data: dict,
        current_cost: Money | None = None,
        _registry: CarParkRegistry | None = None,
    ) -> tuple[Money | None, Money | None, list[CostGroup]]:
        """Look up parking costs when park-and-ride used a driving leg.

        Returns ``(parking_cost, new_daily_cost, cost_groups)`` where
        ``cost_groups`` contains a single ``CostGroup`` with the parking
        fee (operator ``"ParkCo"``) so that ``non_rail_cost()`` on the
        resulting commute reflects the parking cost.

        ``_registry`` \u2014 optional ``CarParkRegistry`` instance.  When
        omitted (production), a default registry loaded from CSV is used.
        Tests pass a pre-populated registry via ``from_car_parks()``.
        """
        journeys = data.get("journeys", [])
        if not journeys:
            return None, current_cost, []
        best = min(journeys, key=lambda j: j.get("duration", 9999))
        legs = best.get("legs", [])
        if not legs or legs[0].get("mode", {}).get("name") != "driving":
            return None, current_cost, []

        station_name = legs[0].get("arrivalPoint", {}).get("commonName", "")
        if not station_name:
            return None, current_cost, []

        station = find_station(station_name)
        if station is None:
            return None, current_cost, []

        parking = _registry or CarParkRegistry()
        car_park = parking.find_car_park(station)

        if car_park is None:
            result = await parking.add_nearest_car_park_for(station)
            car_park = result.value_or_none() if result.succeeded else None
        elif car_park.daily_cost is None:
            result = await parking.load_costs(car_park, station)
            if result.succeeded:
                car_park = result.value_or_none()

        if car_park is None or car_park.daily_cost is None:
            return None, current_cost, []

        parking_cost = car_park.daily_cost
        new_cost = current_cost + parking_cost if current_cost is not None else parking_cost

        parking_group = CostGroup(
            legs=(JourneyLeg(mode=LegMode.PARK, duration=Quantity(0, "minute")),),
            operator=f"ParkCo: {car_park.name}",
            cost=parking_cost,
        )
        return parking_cost, new_cost, [parking_group]

    def _build_cost_groups(self, data: dict) -> list[CostGroup]:
        """Parse TfL response legs into CostGroup objects.

        Walking legs before/after transit and between transit lines
        are boring (no cost).  Transit legs are grouped by the fare
        structure in the API response — if ``fare.fares`` provides
        separate costs per mode, each mode gets its own CostGroup.
        """
        journeys = data.get("journeys", [])
        if not journeys:
            return []
        best = min(journeys, key=lambda j: j.get("duration", 9999))
        tfl_legs = best.get("legs", [])
        if not tfl_legs:
            return []

        fare = best.get("fare", {})
        fare_fares: list[dict] = fare.get("fares", []) if fare else []
        modes_with_fares: set[str] = {f.get("mode") for f in fare_fares if f.get("cost")}
        if fare and fare.get("totalCost") is not None:
            round(fare["totalCost"] / 100.0 * 2, 2)

        groups: list[CostGroup] = []
        current_legs: list[JourneyLeg] = []
        current_mode: str | None = None
        in_transit = False

        parsed = TflClient._parse_tfl_legs(tfl_legs)

        for jl, mode_name in parsed:
            if mode_name == "walking":
                if in_transit:
                    current_legs.append(jl)
                    continue
                groups.append(CostGroup(legs=(jl,)))
                continue

            # Transit leg — check if we need a new CostGroup
            if current_mode is not None and current_mode != mode_name and (modes_with_fares or in_transit):
                groups.append(
                    CostGroup(
                        legs=tuple(current_legs),
                        operator="TfL",
                    )
                )
                current_legs = []

            if not in_transit and current_legs:
                groups.append(CostGroup(legs=tuple(current_legs)))
                current_legs = []
            in_transit = True
            current_mode = mode_name
            current_legs.append(jl)

        if current_legs:
            groups.append(
                CostGroup(
                    legs=tuple(current_legs),
                    operator="TfL",
                )
            )

        return groups
