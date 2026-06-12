"""Commute routing — unified interface for walking, transit, and driving.

The caller describes the traveler; ``get_commute`` handles the rest.
No knowledge of Google, TfL, or ORS leaks to callers.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from houses.api_cache import cached_async_client, get_cached, set_cached
from houses.attempt import Attempt
from houses.bus_journey import BusJourneyRegistry, cheapest_round_trip
from houses.commute import Commute, CommuteMode, CostGroup, JourneyLeg, LegMode
from houses.config import settings
from houses.endpoint_client import EndpointClient
from houses.http_error import HttpError
from houses.retry import retry_async
from houses.stations import Station

logger = logging.getLogger(__name__)

# Module-level singleton for bus fare lookups
_bus_fares = BusJourneyRegistry()


def _bus_fare_for(
    dep_name: str,
    arr_name: str,
    dep_point: dict[str, float] | None = None,
    arr_point: dict[str, float] | None = None,
) -> float | None:
    """Look up daily round-trip bus cost between two stops.

    Delegates to ``BusJourneyRegistry`` which handles direct name match,
    fuzzy token match, and coordinate-based match (100m radius via spatial
    index).  No expanding-radius search — the point of taking the bus is
    to avoid long walks.

    Returns the cost as a float, or ``None`` if no fare is found.
    """
    fares = _bus_fares.fares_for_stops(dep_name, arr_name, dep_point=dep_point, arr_point=arr_point)
    cheapest = cheapest_round_trip(fares, _bus_fares.national_max_single)
    if cheapest is not None:
        return float(cheapest.amount)
    return None


# ---------------------------------------------------------------------------
# API URLs
# ---------------------------------------------------------------------------

GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"


_google_routes = EndpointClient("google-routes", max_retries=3, base_delay=2.0)


async def _google_routes_post(
    body: dict,
    field_mask: str,
    *,
    timeout: float = 10.0,
) -> dict | None:
    """POST to Google Routes API, caching responses and using EndpointClient retry.

    Raises ``ValueError`` if the API key is not configured.
    """
    google_key = settings.google_maps_api_key
    if not google_key:
        raise ValueError("Google Maps API key not configured")

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": google_key,
        "X-Goog-FieldMask": field_mask,
    }
    key = json.dumps(body, sort_keys=True)
    cached = get_cached("POST", GOOGLE_ROUTES_URL, None, key)
    if cached is not None:
        return cached

    async def _do_post() -> dict:
        async with cached_async_client(timeout=timeout) as client:
            resp = await client.post(GOOGLE_ROUTES_URL, json=body, headers=headers)
            if resp.status_code == 429:
                raise HttpError(429, "rate limited", headers=dict(resp.headers))
            resp.raise_for_status()
            return resp.json()

    data = await _google_routes.request(_do_post)
    if data is not None:
        set_cached("POST", GOOGLE_ROUTES_URL, None, key, data)
    return data


# ---------------------------------------------------------------------------
# Congestion zone — central London postcode outcodes never worth driving to
# ---------------------------------------------------------------------------

_CONGESTION_OUTCODES: frozenset[str] = frozenset(
    {
        # EC — all EC districts are inside the zone
        "EC1A",
        "EC1N",
        "EC1R",
        "EC1V",
        "EC1Y",
        "EC2A",
        "EC2N",
        "EC2R",
        "EC2V",
        "EC2Y",
        "EC3A",
        "EC3N",
        "EC3R",
        "EC3V",
        "EC4A",
        "EC4N",
        "EC4M",
        "EC4R",
        "EC4V",
        "EC4Y",
        # WC — all WC districts are inside
        "WC1A",
        "WC1B",
        "WC1E",
        "WC1H",
        "WC1N",
        "WC1R",
        "WC1V",
        "WC1X",
        "WC2A",
        "WC2B",
        "WC2E",
        "WC2H",
        "WC2N",
        "WC2R",
        # W1 — all W1 districts are inside
        "W1A",
        "W1B",
        "W1C",
        "W1D",
        "W1F",
        "W1G",
        "W1H",
        "W1J",
        "W1K",
        "W1M",
        "W1N",
        "W1P",
        "W1R",
        "W1S",
        "W1T",
        "W1U",
        "W1V",
        "W1W",
        "W1X",
        "W1Y",
        # SW1 — all SW1 districts are inside
        "SW1A",
        "SW1E",
        "SW1H",
        "SW1P",
        "SW1V",
        "SW1W",
        "SW1X",
        "SW1Y",
        # SE1 — some SE1 postcodes are inside
        "SE1",
        # N1 — some N1 postcodes are inside
        "N1",
        # E1, E2, E14 — some parts are inside
        "E1",
        "E1W",
        "E2",
        "E14",
    }
)

_OUTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?")


def _outcode_from_postcode(postcode: str) -> str | None:
    m = _OUTCODE_RE.match(postcode.strip().upper())
    return m.group(0) if m else None


def _in_congestion_zone(postcode: str) -> bool:
    oc = _outcode_from_postcode(postcode)
    return oc in _CONGESTION_OUTCODES if oc else False


def _is_london_area(postcode: str) -> bool:
    """Rough check: is this postcode in the TfL service area?"""
    oc = _outcode_from_postcode(postcode)
    if not oc:
        return False
    # All London postcode areas start with E, EC, N, NW, SE, SW, W, WC
    return oc.startswith(("E", "EC", "N", "NW", "SE", "SW", "W", "WC"))


# ---------------------------------------------------------------------------
# Walking — Google Routes walking mode
# ---------------------------------------------------------------------------


async def _walk_commute(origin_postcode: str, dest_postcode: str) -> Commute | None:
    """Try walking via Google Routes walking mode.

    Raises:
        ValueError: If the Google Maps API key is not configured.
    """
    body = {
        "origin": {"address": origin_postcode},
        "destination": {"address": dest_postcode},
        "travelMode": "WALK",
    }
    data = await _google_routes_post(body, "routes.duration,routes.distanceMeters")
    if data is None:
        return None

    routes = data.get("routes", [])
    if not routes:
        return None
    duration_sec = int(routes[0].get("duration", "0s").rstrip("s"))
    duration_min = round(duration_sec / 60)

    return Commute(
        destination_label="",
        destination_postcode=dest_postcode,
        duration_minutes=duration_min,
        daily_cost_gbp=0.0,
        mode=LegMode.WALK,
        cost_groups=(
            CostGroup(
                legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=duration_min),),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Transit — Google Routes transit mode (non-London areas)
# ---------------------------------------------------------------------------


async def _google_transit_commute(origin_postcode: str, dest_postcode: str) -> Commute | None:
    """Transit routing via Google Routes API.

    Parses all transit steps (walk, bus, train, tube) from the Google Routes
    response into ``CostGroup`` / ``JourneyLeg`` objects so the route summary
    is populated.  Also looks up BODS bus fares for any bus legs.

    Raises:
        ValueError: If the Google Maps API key is not configured.
    """
    body = {
        "origin": {"address": origin_postcode},
        "destination": {"address": dest_postcode},
        "travelMode": "TRANSIT",
        "transitPreferences": {"routingPreference": "less_walking"},
        "computeAlternativeRoutes": False,
    }
    field_mask = (
        "routes.duration,routes.legs.steps.staticDuration,"
        "routes.legs.steps.travelMode,routes.legs.steps.transitDetails"
    )
    data = await _google_routes_post(body, field_mask, timeout=15.0)
    if data is None:
        return None

    routes = data.get("routes", [])
    if not routes:
        return None

    duration_sec = int(routes[0].get("duration", "0s").rstrip("s"))
    duration_min = round(duration_sec / 60)
    leg = routes[0].get("legs", [{}])[0]
    steps = leg.get("steps", [])

    # ── Build cost groups from ALL transit steps ───────────────────
    cost_groups: list[CostGroup] = []
    total_bus_cost = 0.0
    current_bus_legs: list[JourneyLeg] = []
    current_walk_legs: list[JourneyLeg] = []

    def _flush_walk():
        if current_walk_legs:
            cost_groups.append(CostGroup(legs=tuple(current_walk_legs)))
            current_walk_legs.clear()

    def _flush_bus():
        if current_bus_legs:
            bus_cost = total_bus_cost if total_bus_cost > 0 else None
            cost_groups.append(CostGroup(legs=tuple(current_bus_legs), cost=bus_cost))
            current_bus_legs.clear()

    for s in steps:
        mode = s.get("travelMode", "WALK")
        dur_raw: str = s.get("staticDuration", "0s")
        dur = int(dur_raw.rstrip("s"))
        dur_min = round(dur / 60)

        if mode == "WALK":
            _flush_bus()
            current_walk_legs.append(JourneyLeg(mode=LegMode.WALK, duration_minutes=dur_min))
        elif mode == "TRANSIT":
            _flush_walk()
            td = s.get("transitDetails", {})
            vtype = td.get("transitLine", {}).get("vehicle", {}).get("type", "")
            dep_stop = td.get("stopDetails", {}).get("departureStop", {}).get("name", "")
            arr_stop = td.get("stopDetails", {}).get("arrivalStop", {}).get("name", "")
            line = td.get("transitLine", {}).get("nameShort", "") or td.get("transitLine", {}).get("name", "")
            desc = f"{line} from {Station.short_name(dep_stop)}".strip() if line and dep_stop else ""

            if vtype == "BUS":
                # Look up BODS bus fare
                dep_point = td.get("stopDetails", {}).get("departureStop", {}).get("location", {}).get("latLng", {})
                arr_point = td.get("stopDetails", {}).get("arrivalStop", {}).get("location", {}).get("latLng", {})
                dp = {"lat": dep_point.get("latitude"), "lon": dep_point.get("longitude")} if dep_point else None
                ap = {"lat": arr_point.get("latitude"), "lon": arr_point.get("longitude")} if arr_point else None
                leg_cost = _bus_fare_for(dep_stop, arr_stop, dep_point=dp, arr_point=ap)
                if leg_cost is not None:
                    total_bus_cost += leg_cost
                current_bus_legs.append(JourneyLeg(mode=LegMode.BUS, duration_minutes=dur_min, description=desc))
            else:
                # Train / Tube / Tram — group with any preceding bus legs
                _flush_bus()
                mode_enum = {
                    "RAIL": LegMode.TRAIN,
                    "TRAIN": LegMode.TRAIN,
                    "HEAVY_RAIL": LegMode.TRAIN,
                    "TRAM": LegMode.TRAIN,
                    "SUBWAY": LegMode.TUBE,
                    "METRO": LegMode.TUBE,
                }.get(vtype, LegMode.TRAIN)
                cost_groups.append(
                    CostGroup(legs=(JourneyLeg(mode=mode_enum, duration_minutes=dur_min, description=desc),))
                )
        # Other modes (e.g. BICYCLE) — ignore

    _flush_walk()
    _flush_bus()

    daily_cost_gbp = round(total_bus_cost, 2) if total_bus_cost > 0 else None

    return Commute(
        destination_label="",
        destination_postcode=dest_postcode,
        duration_minutes=duration_min,
        daily_cost_gbp=daily_cost_gbp,
        mode=CommuteMode.TRANSIT,
        cost_groups=tuple(cost_groups),
    )


# ---------------------------------------------------------------------------
# Driving — ORS Directions
# ---------------------------------------------------------------------------


async def _drive_commute(origin_postcode: str, dest_postcode: str) -> Commute | None:
    """Driving route via ORS Directions API.

    Raises:
        ValueError: If the ORS API key is not configured.
    """
    if not settings.ors_api_key:
        raise ValueError("ORS API key not configured — cannot compute driving route")

    from houses.location import _geocode_address, geocode

    origin_coords = (await geocode(origin_postcode)).value_or_none()
    if origin_coords is None:
        origin_coords = (await _geocode_address(origin_postcode)).value_or_none()
    if origin_coords is None:
        return None

    dest_coords = (await geocode(dest_postcode)).value_or_none()
    if dest_coords is None:
        dest_coords = (await _geocode_address(dest_postcode)).value_or_none()
    if dest_coords is None:
        return None

    coords = [[origin_coords.lon, origin_coords.lat], [dest_coords.lon, dest_coords.lat]]
    body = {"coordinates": coords, "units": "km"}
    key = json.dumps(body, sort_keys=True)

    cached = get_cached("POST", ORS_DIRECTIONS_URL, None, key)
    if cached is not None:
        dir_data = cached
    else:
        try:
            async with cached_async_client(timeout=15.0) as client:
                resp = await retry_async(
                    lambda: client.post(
                        ORS_DIRECTIONS_URL,
                        headers={
                            "Authorization": settings.ors_api_key,
                            "Content-Type": "application/json",
                        },
                        json=body,
                    ),
                    max_retries=3,
                    base_delay=2.0,
                    exceptions=(httpx.HTTPStatusError, httpx.RequestError),
                )
                resp.raise_for_status()
                dir_data = resp.json()
                set_cached("POST", ORS_DIRECTIONS_URL, None, key, dir_data)
        except Exception as e:
            logger.debug("Driving route failed for %s → %s: %s", origin_postcode, dest_postcode, e)
            return None

    try:
        one_way_km = dir_data["routes"][0]["summary"]["distance"]
        one_way_duration_sec = dir_data["routes"][0]["summary"]["duration"]
        round_trip_km = round(one_way_km * 2, 1)
        duration_minutes = round(one_way_duration_sec / 60)  # one-way
        litres_per_100km = 235.214 / settings.petrol_mpg
        litres_used = (round_trip_km / 100) * litres_per_100km
        cost = round(litres_used * settings.petrol_price_per_litre, 2)

        return Commute(
            destination_label="",
            destination_postcode=dest_postcode,
            duration_minutes=duration_minutes,
            daily_cost_gbp=cost,
            mode=CommuteMode.DRIVE,
            cost_groups=(
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.DRIVE, duration_minutes=duration_minutes),),
                    cost=cost,
                ),
            ),
        )
    except (KeyError, IndexError) as e:
        logger.debug("Failed to parse ORS response: %s", e)
        return None


# ---------------------------------------------------------------------------
# Bus alternative — Google Routes for non-TfL areas
# ---------------------------------------------------------------------------


async def _find_bus_alternative(origin: str, destination: str) -> Commute | None:
    """Find a bus alternative via Google Routes API (for areas outside TfL coverage).

    Used when TfL doesn't find a bus leg (out-of-London areas). Returns a
    Commute with bus fare looked up from BODS data, or None if the API
    also can't route the journey.
    """
    body = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": "TRANSIT",
        "transitPreferences": {"routingPreference": "less_walking"},
        "computeAlternativeRoutes": False,
    }

    data = await _google_routes_post(body, "routes.duration,routes.legs", timeout=15.0)
    if data is None:
        return None

    routes = data.get("routes", [])
    if not routes:
        return None

    leg = routes[0].get("legs", [{}])[0]
    duration_sec = int(routes[0].get("duration", "0s").rstrip("s"))
    duration_min = round(duration_sec / 60)

    steps = leg.get("steps", [])
    bus_legs = [
        s
        for s in steps
        if s.get("travelMode") == "TRANSIT"
        and s.get("transitDetails", {}).get("transitLine", {}).get("vehicle", {}).get("type") == "BUS"
    ]

    total_bus_cost = 0.0
    bus_cost_gbp = None
    for bl in bus_legs:
        transit = bl.get("transitDetails", {})
        dep_stop = transit.get("stopDetails", {}).get("departureStop", {})
        arr_stop = transit.get("stopDetails", {}).get("arrivalStop", {})
        dep_name = dep_stop.get("name", "")
        arr_name = arr_stop.get("name", "")
        dep_coords = dep_stop.get("location", {}).get("latLng", {})
        arr_coords = arr_stop.get("location", {}).get("latLng", {})
        dep_point = {"lat": dep_coords.get("latitude"), "lon": dep_coords.get("longitude")} if dep_coords else None
        arr_point = {"lat": arr_coords.get("latitude"), "lon": arr_coords.get("longitude")} if arr_coords else None
        leg_cost = _bus_fare_for(dep_name, arr_name, dep_point=dep_point, arr_point=arr_point)
        if leg_cost is not None:
            total_bus_cost += leg_cost

    if total_bus_cost > 0:
        bus_cost_gbp = total_bus_cost
        daily_cost_gbp = round(total_bus_cost, 2)
    else:
        daily_cost_gbp = None

    return Commute(
        destination_label="Lorena — Aldgate / City of London (Bus)",
        destination_postcode=destination,
        duration_minutes=duration_min,
        daily_cost_gbp=daily_cost_gbp,
        mode="transit",
        cost_groups=(
            CostGroup(
                legs=(JourneyLeg(mode=LegMode.BUS, duration_minutes=duration_min or 0),),
                cost=bus_cost_gbp,
            ),
        )
        if bus_cost_gbp is not None
        else (),
    )


# ---------------------------------------------------------------------------
# Transit — TfL via TransitRoute (London area)
# ---------------------------------------------------------------------------


async def _tfl_transit_commute(origin_postcode: str, dest_postcode: str, has_car: bool) -> Commute | None:
    """Transit routing via TfL API.

    Tries routes with and without bus mode, picks the best.
    Also applies bus fallback via Google Routes when the first-leg
    walk exceeds max_walk_minutes (TfL doesn't cover some areas).
    """
    from houses.transit_route import TransitRoute

    label = f"{origin_postcode} → {dest_postcode}"
    no_bus = await TransitRoute(origin_postcode, dest_postcode, label, park_and_ride=has_car).plan()
    with_bus = await TransitRoute(origin_postcode, dest_postcode, label, park_and_ride=has_car, allow_bus=True).plan()

    if no_bus.is_impossible and with_bus.is_impossible:
        return None

    empty = Commute(destination_label=label, destination_postcode=dest_postcode)
    no_bus_val = no_bus.value_or(empty)
    with_bus_val = with_bus.value_or(empty)
    result = _pick_best_route(no_bus_val, with_bus_val)

    # Bus fallback: if the chosen route has a long walk to the first
    # transit leg, try Google Routes transit as an alternative.
    if result is no_bus_val and no_bus_val.duration_minutes is not None:
        m = re.search(r"walk.*?\((\d+)m\)", no_bus_val.summary()[:60])
        walk_to_station = int(m.group(1)) if m else 0
        if walk_to_station >= settings.bus_walk_penalty_minutes:
            bus = await _find_bus_alternative(origin_postcode, dest_postcode)
            if bus is not None and bus.non_rail_cost() > 0:
                bus_time = min(15, walk_to_station - settings.bus_walk_penalty_minutes)
                savings = walk_to_station - bus_time
                if savings >= settings.bus_walk_penalty_minutes:
                    new_duration = no_bus_val.duration_minutes - walk_to_station + bus_time
                    new_cost = no_bus_val.daily_cost_gbp
                    bus_cost = bus.non_rail_cost()
                    new_cost = round(new_cost + bus_cost, 2) if new_cost is not None else bus_cost
                    result = Commute(
                        destination_label=label,
                        destination_postcode=dest_postcode,
                        duration_minutes=new_duration,
                        daily_cost_gbp=new_cost,
                        mode="transit",
                        cost_groups=(
                            CostGroup(
                                legs=(JourneyLeg(mode=LegMode.BUS, duration_minutes=bus_time),),
                                cost=bus_cost,
                            ),
                        ),
                    )

    return result


def _pick_best_route(a: Commute, b: Commute) -> Commute:
    """Pick the better of two transit route options.

    Uses the ``b`` result only if it saves at least
    ``bus_walk_penalty_minutes`` over ``a``.
    """
    if b.duration_minutes is None:
        return a
    if a.duration_minutes is None:
        return b
    savings = a.duration_minutes - b.duration_minutes
    if savings >= settings.bus_walk_penalty_minutes:
        return b
    return a


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_commute(
    origin_postcode: str,
    dest_postcode: str,
    *,
    has_car: bool,
    max_walk_minutes: int,
) -> Attempt[Commute]:
    """Route from origin to destination based on the traveler's circumstances.

    Parameters:
        origin_postcode: Where the traveler starts.
        dest_postcode: Where the traveler is going.
        has_car: Whether the traveler has access to a car.
        max_walk_minutes: Maximum acceptable walking time for the first/last
            leg. Beyond this, transit or driving is preferred.

    Returns an ``Attempt[Commute]``.  When no route is available or a backend
    fails, the attempt carries the source and reason (e.g. ``"google_routes"``,
    ``"API rate limited (429)"``).

    Optimisation order (cheapest API calls first):
        0. Congestion zone check — skip driving for central London.
        1. Walking API — if ``≤ max_walk_minutes``, return immediately.
        2. Transit (TfL for London, Google Routes otherwise).
        3. If ``has_car`` and destination is NOT congestion zone: driving.
        4. Pick the quicker among available options.
        5. If no car and transit unavailable → ``None``.
    """
    candidates: list[Commute] = []
    failures: list[str] = []

    # ── 0. Congestion zone — skip driving ──────────────────────────
    dest_in_congestion = _in_congestion_zone(dest_postcode)

    # ── 1. Walking (cheapest to try) ───────────────────────────────
    try:
        walk = await _walk_commute(origin_postcode, dest_postcode)
    except ValueError as e:
        failures.append(f"walk: {e}")
        walk = None
    if walk is not None and walk.duration_minutes is not None and walk.duration_minutes <= max_walk_minutes:
        return Attempt.succeeded(walk, "walk")
    if walk is not None:
        candidates.append(walk)

    # ── 2. Transit ─────────────────────────────────────────────────
    # Try Google Routes first (covers all UK buses, unlike TfL which only
    # knows about London).  Also try TfL for London destinations as it
    # sometimes has more detailed London-specific routing.
    google: Commute | None = None
    tfl: Commute | None = None

    try:
        google = await _google_transit_commute(origin_postcode, dest_postcode)
    except ValueError as e:
        logger.warning("Google transit skipped: %s", e)
        failures.append(f"google_transit: {e}")

    # Google Routes covers all UK buses and returns cost data when BODS
    # fares are available.  If Google already returned a route with pricing,
    # there's no need to also call TfL — we save an API call.
    google_has_pricing = google is not None and google.daily_cost_gbp is not None and google.daily_cost_gbp > 0
    if not google_has_pricing and _is_london_area(dest_postcode):
        try:
            tfl = await _tfl_transit_commute(origin_postcode, dest_postcode, has_car)
        except Exception as e:
            logger.warning("TfL transit failed for %s → %s: %s", origin_postcode, dest_postcode, e)
            failures.append(f"tfl_transit: {e}")

    candidates.extend(c for c in (google, tfl) if c is not None)

    # ── 3. Driving ─────────────────────────────────────────────────
    if has_car and not dest_in_congestion:
        try:
            drive = await _drive_commute(origin_postcode, dest_postcode)
        except ValueError as e:
            failures.append(f"drive: {e}")
            drive = None
        if drive is not None:
            candidates.append(drive)

    # ── 4. Pick fastest ────────────────────────────────────────────
    valid = [c for c in candidates if c.duration_minutes is not None]
    if valid:
        # Prefer priced routes over faster non-priced ones.
        # Priority:
        #   1. Has real cost data (non-None, non-zero)
        #   2. Faster duration
        # Google Routes may return the fastest transit option but often
        # lacks bus/rail fare data (cost=None).  TfL has accurate
        # pricing for London.  When we have both, the priced result is
        # more useful — the NR fare fallback (applied later) can only
        # approximate a rail fare and won't capture bus costs.
        def _tiebreak(c: Commute) -> tuple[int, float]:
            no_cost = 1 if (c.daily_cost_gbp is None or c.daily_cost_gbp == 0.0) else 0
            return (no_cost, c.duration_minutes or 0)
        return Attempt.succeeded(min(valid, key=_tiebreak), "routing")

    reason = "; ".join(failures) if failures else "no route available"
    return Attempt.impossible("routing", reason)


def _with_label(commute: Commute, label: str, postcode: str) -> Commute:
    """Set destination label on a commute (Commute is frozen, so replace)."""
    import dataclasses

    return dataclasses.replace(commute, destination_label=label, destination_postcode=postcode)
