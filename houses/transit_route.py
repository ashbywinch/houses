"""TransitRoute — public-transit route planning via the TfL API."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from datetime import datetime, timedelta

import httpx

from houses.api_cache import cached_async_client, get_cached, set_cached
from houses.attempt import Attempt
from houses.car_park import CarParkRegistry
from houses.commute import Commute, CostGroup, JourneyLeg, LegMode
from houses.config import settings
from houses.location import _geocode_address, geocode
from houses.retry import retry_async
from houses.routing import _bus_fare_for
from houses.stations import Station
from houses.stations import find as find_station

logger = logging.getLogger(__name__)

TFL_JOURNEY_URL = "https://api.tfl.gov.uk/Journey/JourneyResults"
OUTCODES_IO_URL = "https://api.postcodes.io/outcodes"
POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


# ---------------------------------------------------------------------------
# TfL helper functions
# ---------------------------------------------------------------------------


def _next_weekday_date_params() -> dict[str, str]:
    """Return ``date`` and ``time`` params for the next upcoming weekday at 09:00."""
    now = datetime.now()
    if now.weekday() < 5 and now.hour < 9:
        return {"date": now.strftime("%Y%m%d"), "time": "0900"}
    target = now + timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return {"date": target.strftime("%Y%m%d"), "time": "0900"}


def _tfl_auth_params() -> dict[str, str]:
    params = {}
    if settings.tfl_api_key:
        params["app_key"] = settings.tfl_api_key
    return params


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
            label = f"bus({bus_num}) to {clean_arr} ({duration}m)" if bus_num else f"Bus to {clean_arr} ({duration}m)"
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

    return " → ".join(parts)


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
    route_summary = _format_route_summary(best)
    return duration, cost, route_summary


async def _get_drive_minutes(origin_postcode: str, station_name: str) -> int | None:
    origin_coords = (await geocode(origin_postcode)).value_or_none()
    if origin_coords is None:
        origin_coords = (await _geocode_address(origin_postcode)).value_or_none()
    if origin_coords is None:
        return None

    station = find_station(station_name)
    dest_coords = station.location if station else None
    if dest_coords is None:
        dest_coords = (await _geocode_address(station_name)).value_or_none()
    if dest_coords is None:
        return None

    dest_lat = dest_coords.lat
    dest_lng = dest_coords.lon

    body = {
        "coordinates": [[origin_coords.lon, origin_coords.lat], [dest_lng, dest_lat]],
        "units": "km",
    }
    try:
        async with cached_async_client(timeout=15.0) as client:
            cached = get_cached("POST", ORS_DIRECTIONS_URL, None, json.dumps(body, sort_keys=True))
            if cached is not None:
                return round(cached["routes"][0]["summary"]["duration"] / 60)
            resp = await retry_async(
                lambda: client.post(
                    ORS_DIRECTIONS_URL,
                    headers={"Authorization": settings.ors_api_key, "Content-Type": "application/json"},
                    json=body,
                ),
                max_retries=2,
                base_delay=1.0,
                exceptions=(httpx.HTTPStatusError, httpx.RequestError),
            )
            resp.raise_for_status()
            data = resp.json()
            set_cached("POST", ORS_DIRECTIONS_URL, None, json.dumps(body, sort_keys=True), data)
            return round(data["routes"][0]["summary"]["duration"] / 60)
    except Exception:
        logger.warning("Park-and-ride ORS lookup failed for %s → %s", origin_postcode, station_name)
        return None


async def _apply_park_and_ride_to_journeys(
    data: dict,
    origin_postcode: str,
    max_walk_minutes: int,
) -> dict:
    journeys = data.get("journeys", [])
    if not journeys:
        return data
    for journey in journeys:
        legs = journey.get("legs", [])
        if not legs:
            continue
        first = legs[0]
        if first.get("mode", {}).get("name") != "walking":
            continue
        walk_duration = first.get("duration", 0)
        logger.debug(
            "park_and_ride: walk leg=%dm to station='%s' threshold=%dm",
            walk_duration,
            first.get("arrivalPoint", {}).get("commonName", "?"),
            max_walk_minutes,
        )
        if walk_duration <= max_walk_minutes:
            logger.debug("park_and_ride: walk %dm <= %dm threshold — keeping walk", walk_duration, max_walk_minutes)
            continue
        station_name = first.get("arrivalPoint", {}).get("commonName", "")
        if not station_name:
            logger.debug("park_and_ride: walk leg has no arrivalPoint — skipping")
            continue
        drive_minutes = await _get_drive_minutes(origin_postcode, station_name)
        if drive_minutes is None:
            logger.debug(
                "park_and_ride: ORS returned None for '%s' -> '%s' — keeping walk",
                origin_postcode,
                station_name,
            )
            continue
        logger.debug(
            "park_and_ride: replacing walk %dm with drive %dm to '%s'",
            walk_duration,
            drive_minutes,
            station_name,
        )
        legs[0] = {
            "mode": {"name": "driving"},
            "duration": drive_minutes,
            "instruction": {"summary": f"Drive to {station_name}"},
            "arrivalPoint": first.get("arrivalPoint"),
        }
        old_duration = journey.get("duration", 0)
        journey["duration"] = old_duration - walk_duration + drive_minutes
    return data


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


class TransitRoute:
    """A public-transit route between two places in greater London.

    Usage::

        route = TransitRoute(
            origin="GU21 7QF",
            destination="SW1V 2QQ",
            label="Simon — Pimlico / Victoria",
            park_and_ride=True,
        )
        commute: Attempt[Commute] = await route.plan()
    """

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

    async def plan(self) -> Attempt[Commute]:
        """Fetch TfL route, pick best journey, enrich with costs.

        Returns ``Attempt.succeeded(Commute(...))`` on success,
        ``Attempt.impossible("tfl", ...)`` on failure.
        """
        modes = ["tube", "overground", "dlr", "tram", "national-rail", "walking"]
        if self._allow_bus:
            modes.append("bus")

        url = f"{TFL_JOURNEY_URL}/{self._origin}/to/{self._destination}"
        params = {
            "nationalSearch": "true",
            "timeIs": "arriving",
            "journeyPreference": "leasttime",
            "mode": ",".join(modes),
            **_next_weekday_date_params(),
            **_tfl_auth_params(),
        }
        # Cache key must NOT include API keys — TfL responses are identical
        # regardless of which key is used.
        cache_params = {k: v for k, v in params.items() if k != "app_key"}

        duration_minutes: int | None = None
        daily_cost_gbp: float | None = None
        route_summary = ""
        parking_cost_gbp: float | None = None
        bus_cost_gbp: float | None = None
        cost_groups: list[CostGroup] = []
        data: dict | None = None

        # Check cache first
        cached = get_cached("GET", url, cache_params)
        if cached is not None:
            # Cache hit — if the cached response is a disambiguation, handle it
            # the same way as a live 300 (triggers geocode fallback).
            if "Disambiguation" in str(cached.get("$type", "")):
                data = await self._geocode_fallback(params)
                if data is None:
                    logger.warning("TfL disambiguation from cache, fallback failed for %s", self._label)
            else:
                data = cached
        else:
            try:
                async with cached_async_client(timeout=20.0) as client:
                    resp = await retry_async(
                        lambda: client.get(url, params=params),
                        max_retries=2,
                        base_delay=1.0,
                        exceptions=(httpx.HTTPStatusError, httpx.RequestError),
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    set_cached("GET", url, cache_params, None, data)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 300:
                    # Cache the 300 response itself so we don't hammer the API
                    # on subsequent requests — the cache-hit path above knows
                    # how to handle disambiguation results.
                    with contextlib.suppress(Exception):
                        set_cached("GET", url, cache_params, None, e.response.json())
                    data = await self._geocode_fallback(params)
                elif e.response.status_code != 404:
                    logger.error("TfL API HTTP error for %s: %s", self._label, e)
            except httpx.RequestError as e:
                logger.error("TfL API request failed for %s: %s", self._label, e)
            except (KeyError, IndexError, TypeError) as e:
                logger.error("TfL API unexpected response for %s: %s", self._label, e)

        if data is not None and self._park_and_ride:
            data = await _apply_park_and_ride_to_journeys(data, self._origin, settings.max_walk_to_station_minutes)

        if data is not None:
            duration_minutes, daily_cost_gbp, route_summary = _pick_best_journey(data)
            cost_groups = self._build_cost_groups(data)

        # Bus fare
        if self._allow_bus and duration_minutes is not None and data is not None:
            bus_cost_gbp = self._add_bus_fare(data, daily_cost_gbp)
            daily_cost_gbp = bus_cost_gbp

        # Parking cost
        if self._park_and_ride and duration_minutes is not None and data is not None:
            parking_cost_gbp, daily_cost_gbp, parking_groups = await self._add_parking_cost(data, daily_cost_gbp)
            cost_groups.extend(parking_groups)

        result = Commute(
            destination_label=self._label,
            destination_postcode=self._destination,
            duration_minutes=duration_minutes,
            daily_cost_gbp=daily_cost_gbp,
            mode="transit",
            cost_groups=tuple(cost_groups),
        )
        if duration_minutes is not None:
            return Attempt.succeeded(result, "tfl")
        return Attempt.impossible("tfl", "could not route transit")

    # ── Internal methods ────────────────────────────────────────

    async def _geocode_fallback(self, params: dict) -> dict | None:
        """Handle TfL 300 response by geocoding the origin and retrying."""
        pc_match = re.search(r"[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?", self._origin)
        pc = pc_match.group(0).strip().upper() if pc_match else None
        coords = (await _geocode_address(self._origin)).value_or_none()
        if coords is None and pc:
            coords = (await geocode(pc)).value_or_none()
        if coords:
            url2 = f"{TFL_JOURNEY_URL}/{coords.lat},{coords.lon}/to/{self._destination}"
            try:
                async with cached_async_client(timeout=20.0) as c2:
                    r2 = await c2.get(url2, params=params)
                    r2.raise_for_status()
                    d2 = r2.json()
                    cache_params2 = {k: v for k, v in params.items() if k != "app_key"}
                    set_cached("GET", url2, cache_params2, None, d2)
                    return d2
            except Exception:
                logger.warning("TfL geocode fallback failed for %s", self._label)
        return None

    def _add_bus_fare(self, data: dict, current_cost: float | None) -> float | None:
        """Look up bus leg costs when TfL didn't price them."""
        journeys = data.get("journeys", [])
        if not journeys:
            return None
        best = min(journeys, key=lambda j: j.get("duration", 9999))
        bus_legs = [leg for leg in best.get("legs", []) if leg.get("mode", {}).get("name") == "bus"]
        if not bus_legs:
            return None

        fare = best.get("fare", {})
        tfl_total_pence = fare.get("totalCost") if fare else None

        if tfl_total_pence and tfl_total_pence > 0:
            return round(tfl_total_pence / 100 * 2, 2)

        tfl_non_bus_fare = 0
        fare_fares = fare.get("fares", []) if fare else []
        for f in fare_fares:
            if f.get("mode") != "bus" and f.get("cost"):
                tfl_non_bus_fare += f["cost"]

        total_bus_cost = 0.0
        for bus_leg in bus_legs:
            dep = bus_leg.get("departurePoint", {}).get("commonName", "")
            arr = bus_leg.get("arrivalPoint", {}).get("commonName", "")
            dep_raw = bus_leg.get("departurePoint", {})
            arr_raw = bus_leg.get("arrivalPoint", {})
            dep_point = {"lat": dep_raw["lat"], "lon": dep_raw["lon"]} if dep_raw.get("lat") else None
            arr_point = {"lat": arr_raw["lat"], "lon": arr_raw["lon"]} if arr_raw.get("lat") else None
            leg_cost = _bus_fare_for(dep, arr, dep_point=dep_point, arr_point=arr_point)
            if leg_cost is not None:
                total_bus_cost += leg_cost

        if total_bus_cost > 0:
            return round(tfl_non_bus_fare / 100 * 2 + total_bus_cost, 2)
        return current_cost

    async def _add_parking_cost(
        self,
        data: dict,
        current_cost: float | None,
    ) -> tuple[float | None, float | None, list[CostGroup]]:
        """Look up parking costs when park-and-ride used a driving leg.

        Returns ``(parking_cost, new_daily_cost, cost_groups)`` where
        ``cost_groups`` contains a single ``CostGroup`` with the parking
        fee (operator ``"ParkCo"``) so that ``non_rail_cost()`` on the
        resulting commute reflects the parking cost.
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

        parking = CarParkRegistry()
        car_park = parking.find_car_park(station)

        if car_park is None:
            result = await parking.add_nearest_car_park_for(station)
            car_park = result.value_or_none() if result.is_succeeded else None
        elif car_park.daily_cost is None:
            result = await parking.load_costs(car_park, station)
            if result.is_succeeded:
                car_park = result.value_or_none()

        if car_park is None or car_park.daily_cost is None:
            return None, current_cost, []

        parking_cost = float(car_park.daily_cost.amount)
        new_cost = current_cost
        if new_cost is not None:
            new_cost = round(new_cost + parking_cost, 2)

        parking_group = CostGroup(
            legs=(JourneyLeg(mode=LegMode.PARK, duration_minutes=0),),
            operator="ParkCo",
            cost=car_park.daily_cost,  # store Money, not float — avoids precision leaks
        )
        return parking_cost, new_cost, [parking_group]

    def _build_cost_groups(self, data: dict) -> list[CostGroup]:
        """Parse TfL response legs into CostGroup objects.

        Walking legs before/after transit and between transit lines
        are boring (no cost). Transit legs are grouped by operator
        (typically one TfL CostGroup covers all transit legs).
        """
        journeys = data.get("journeys", [])
        if not journeys:
            return []
        best = min(journeys, key=lambda j: j.get("duration", 9999))
        tfl_legs = best.get("legs", [])
        if not tfl_legs:
            return []

        groups: list[CostGroup] = []
        current_legs: list[JourneyLeg] = []
        in_transit = False

        parsed = _parse_tfl_legs(tfl_legs)

        for jl, mode_name in parsed:
            if mode_name == "walking":
                if in_transit:
                    current_legs.append(jl)
                else:
                    groups.append(CostGroup(legs=(jl,)))
            else:
                if not in_transit and current_legs:
                    groups.append(CostGroup(legs=tuple(current_legs)))
                    current_legs = []
                in_transit = True
                current_legs.append(jl)

        if current_legs:
            fare = best.get("fare", {})
            cost = None
            if fare and fare.get("totalCost") is not None:
                cost = round(fare["totalCost"] / 100.0 * 2, 2)
            groups.append(CostGroup(legs=tuple(current_legs), operator="TfL", cost=cost))

        return groups


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
            duration_minutes=duration,
            start_station=dep_station,
            end_station=arr_station,
            line_name=line_name,
        )
        result.append((jl, mode_name))
    return result
