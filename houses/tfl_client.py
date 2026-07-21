"""TfL API client for public-transit route planning in London."""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import datetime, timedelta

import httpx
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from houses.api_cache import cached_async_client, get_cached, set_cached
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
        fare_lookup: callable | None = None,
    ):
        self._origin = origin_postcode
        self._destination = destination_postcode
        self._label = label
        self._park_and_ride = park_and_ride
        self._allow_bus = allow_bus
        self._fare_lookup = fare_lookup

    # ── Public API ──────────────────────────────────────────────────

    async def plan(self) -> Attempt[Commute]:
        """Fetch TfL route, enrich with costs, and return a Commute."""
        data = await self._fetch_data()
        if data is not None and self._park_and_ride:
            data = await _apply_park_and_ride_to_journeys(data, self._origin, settings.max_walk_to_station_minutes)
        return await self._process_data(data)

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

        cached = get_cached("GET", url, params)
        if cached is not None:
            return TflClient._parse_tube_fare(cached)

        try:
            async with cached_async_client(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 404:
                    logger.debug("TfL cannot route %s \u2192 %s (walking distance)", from_station.crs, to_postcode)
                    return None
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", url, params, None, data)
                return TflClient._parse_tube_fare(data)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug("TfL cannot route %s \u2192 %s (walking distance)", from_station.crs, to_postcode)
                return None
            logger.warning("TfL tube leg fare failed for %s \u2192 %s: %s", from_station.crs, to_postcode, e)
            return None
        except Exception as e:
            logger.debug("TfL tube leg fare failed for %s \u2192 %s: %s", from_station.crs, to_postcode, e)
            return None

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
        """Return ``date`` and ``time`` params for the next upcoming weekday at 09:00."""
        now = datetime.now()
        if now.weekday() < 5 and now.hour < 9:
            return {"date": now.strftime("%Y%m%d"), "time": "0900"}
        target = now + timedelta(days=1)
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
                duration_minutes=duration,
                start_station=dep_station,
                end_station=arr_station,
                line_name=line_name,
            )
            result.append((jl, mode_name))
        return result

    # ── Internal fetch / process ─────────────────────────────────────

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
        cache_params = {k: v for k, v in params.items() if k != "app_key"}

        cached = get_cached("GET", url, cache_params)
        if cached is not None:
            if "Disambiguation" in str(cached.get("$type", "")):
                data = await self._geocode_fallback(params)
                if data is None:
                    logger.warning("TfL disambiguation from cache, fallback failed for %s", self._label)
            else:
                data = cached
            return data

        try:
            async with cached_async_client(timeout=20.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                set_cached("GET", url, cache_params, None, data)
                return data
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 300:
                with contextlib.suppress(Exception):
                    set_cached("GET", url, cache_params, None, e.response.json())
                return await self._geocode_fallback(params)
            elif e.response.status_code == 429 or (500 <= e.response.status_code < 600):
                raise  # transient \u2014 let DAG retry handle it
            elif e.response.status_code != 404:
                logger.error("TfL API HTTP error for %s (url=%s): %s", self._label, url, e)
        except httpx.RequestError:
            raise  # transient \u2014 let DAG retry handle it
        except (KeyError, IndexError, TypeError) as e:
            logger.error("TfL API unexpected response for %s: %s", self._label, e)
        return None

    async def _process_data(self, data: dict | None) -> Attempt[Commute]:
        """Turn raw TfL API data into a Commute.  Pure logic \u2014 no HTTP.

        Extracted from plan() so tests can pass controlled JSON directly
        without real API calls or monkeypatching.
        """
        duration_minutes: int | None = None
        daily_cost_gbp: Money | None = None
        cost_groups: list[CostGroup] = []

        if data is not None:
            dur, raw_cost, _ = TflClient._pick_best_journey(data)
            duration_minutes = dur
            daily_cost_gbp = Money(str(raw_cost), "GBP") if raw_cost is not None else None
            cost_groups = self._build_cost_groups(data)

        # Bus fare
        if self._allow_bus and duration_minutes is not None and data is not None:
            raw_cost = float(daily_cost_gbp.amount) if daily_cost_gbp is not None else None
            bus_cost_gbp = self._add_bus_fare(data, raw_cost)
            daily_cost_gbp = Money(str(bus_cost_gbp), "GBP") if bus_cost_gbp is not None else None

        # Parking cost
        if self._park_and_ride and duration_minutes is not None and data is not None:
            raw_cost = float(daily_cost_gbp.amount) if daily_cost_gbp is not None else None
            parking_cost_gbp, new_cost, parking_groups = await self._add_parking_cost(data, raw_cost)
            daily_cost_gbp = Money(str(new_cost), "GBP") if new_cost is not None else None
            cost_groups.extend(parking_groups)

        # Ensure daily_cost is never None \u2014 downstream code expects Money
        if daily_cost_gbp is None:
            daily_cost_gbp = Money("0", "GBP")

        result = Commute(
            person=Person(name="", has_car=False),
            label=self._label,
            destination=PlaceOfInterest(label=self._label, postcode=self._destination),
            duration=Quantity(duration_minutes, "minute") if duration_minutes is not None else None,
            daily_cost=daily_cost_gbp,
            mode="transit",
            details=tuple(cost_groups),
        )
        if duration_minutes is not None:
            return Attempt.succeeded(result)
        return Attempt.impossible("could not route transit")

    async def _geocode_fallback(self, params: dict) -> dict | None:
        """Handle TfL 300 response by geocoding the origin and retrying."""
        pc_match = re.search(r"[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?", self._origin)
        pc = pc_match.group(0).strip().upper() if pc_match else None
        coords = (await _geocode_address(self._origin)).value_or_none()
        if coords is None and pc:
            coords = (await geocode(pc)).value_or_none()
        if coords:
            url2 = f"{TflClient.TFL_JOURNEY_URL}/{coords.lat},{coords.lon}/to/{self._destination}"
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

    # ── Cost helpers ─────────────────────────────────────────────────

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
            leg_cost = (
                self._fare_lookup(dep, arr, dep_point=dep_point, arr_point=arr_point) if self._fare_lookup else None
            )
            if leg_cost is not None:
                total_bus_cost += leg_cost

        if total_bus_cost > 0:
            return round(tfl_non_bus_fare / 100 * 2 + total_bus_cost, 2)
        return current_cost

    async def _add_parking_cost(
        self,
        data: dict,
        current_cost: float | None,
        _registry: CarParkRegistry | None = None,
    ) -> tuple[float | None, float | None, list[CostGroup]]:
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

        parking_cost = float(car_park.daily_cost.amount)
        new_cost = current_cost
        if new_cost is not None:
            new_cost = round(new_cost + parking_cost, 2)

        parking_group = CostGroup(
            legs=(JourneyLeg(mode=LegMode.PARK, duration_minutes=0),),
            operator=f"ParkCo: {car_park.name}",
            cost=car_park.daily_cost,
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

        parsed = TflClient._parse_tfl_legs(tfl_legs)

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
