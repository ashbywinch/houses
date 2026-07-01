"""Commute routing — unified interface for walking, transit, and driving.

The caller describes the traveler; ``get_commute`` handles the rest.
No knowledge of Google, TfL, or ORS leaks to callers.
"""

from __future__ import annotations

import json
import logging
import re

from money import Money

from dag.attempt import Attempt
from houses.api_cache import cached_async_client, get_cached, set_cached
from houses.bus_journey import cheapest_round_trip
from houses.commute import Commute, CostGroup, JourneyLeg, LegMode
from houses.config import settings
from houses.endpoint_client import EndpointClient
from houses.geo import GeoPoint
from houses.http_error import HttpError

logger = logging.getLogger(__name__)


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
    from houses.context import get_bus_fare_reader

    fares_r = get_bus_fare_reader()
    fares = fares_r.fares_for_stops(dep_name, arr_name, dep_point=dep_point, arr_point=arr_point)
    cheapest = cheapest_round_trip(fares, fares_r.national_max_single)
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
        # SE1, N1, E1, E2, E14 — excluded because large parts are outside
        # the zone boundary (Bermondsey, Angel, Bethnal Green, Canary Wharf).
        # The tiebreak in get_commute() will prefer TfL transit for these
        # destinations anyway, but driving remains a valid option.
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


async def _walk_to_station_minutes(
    origin_postcode: str,
    lat: float,
    lng: float,
    *,
    origin_latlng: tuple[float, float] | None = None,
) -> int | None:
    """Walking duration (minutes) from a postcode or coordinate to a lat/lng point.

    Uses Google Routes walking mode.  When ``origin_latlng`` is provided, it is
    used as the origin (a precise coordinate) instead of ``origin_postcode``
    which relies on geocoding the address string.  Returns ``None`` if the
    API call fails or returns no route.
    """
    if origin_latlng is not None:
        origin = {"location": {"latLng": {"latitude": origin_latlng[0], "longitude": origin_latlng[1]}}}
    else:
        origin = {"address": origin_postcode}
    body = {
        "origin": origin,
        "destination": {"location": {"latLng": {"latitude": lat, "longitude": lng}}},
        "travelMode": "WALK",
    }
    data = await _google_routes_post(body, "routes.duration,routes.distanceMeters")
    if data is None:
        return None
    routes = data.get("routes", [])
    if not routes:
        return None
    return round(int(routes[0].get("duration", "0s").rstrip("s")) / 60)


async def _google_route_commute(origin: str | GeoPoint, dest: str | GeoPoint,
                                 mode: str, max_walk_minutes: int | None = None) -> Commute | None:
    """Try walking or driving via Google Routes API.

    Skips the API call entirely when the straight-line distance makes
    walking infeasible (exceeds ``max_walk_minutes`` at 5 km/h).
    """
    if mode == "WALK" and max_walk_minutes is not None:
        max_walk_km = max_walk_minutes * 5.0 / 60.0  # 5 km/h walking pace
        if isinstance(origin, GeoPoint) and isinstance(dest, GeoPoint):
            dist_km = origin.distance_km_to(dest)
            if dist_km > max_walk_km:
                return None
        # If dest is a postcode string we can't check distance — let the API decide

    body = {
        "origin": _address_waypoint(origin),
        "destination": _address_waypoint(dest),
        "travelMode": mode,
    }
    mask = "routes.duration,routes.legs" if mode == "DRIVE" else "routes.duration,routes.distanceMeters"
    data = await _google_routes_post(body, mask, timeout=15.0 if mode == "DRIVE" else 10.0)
    if data is None:
        return None

    routes = data.get("routes", [])
    if not routes:
        return None
    duration_sec = int(routes[0].get("duration", "0s").rstrip("s"))
    duration_min = round(duration_sec / 60)

    dest_str = dest if isinstance(dest, str) else f"{dest.lat},{dest.lon}"
    daily = None
    if mode == "WALK":
        leg = JourneyLeg(mode=LegMode.WALK, duration_minutes=duration_min)
        daily = Money("0", "GBP")
    else:
        leg = JourneyLeg(mode=LegMode.DRIVE, duration_minutes=duration_min)
    return Commute(
        destination_label="",
        destination_postcode=dest_str,
        duration_minutes=duration_min,
        daily_cost_gbp=daily,
        mode=LegMode.WALK if mode == "WALK" else LegMode.DRIVE,
        cost_groups=(
            CostGroup(
                legs=(leg,),
            ),
        ),
    )

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

    total_bus_cost = 0.0
    bus_cost_gbp = None
    for s in steps:
        if s.get("travelMode") != "TRANSIT":
            continue
        td = s.get("transitDetails", {})
        if td.get("transitLine", {}).get("vehicle", {}).get("type") != "BUS":
            continue
        dep_stop = td.get("stopDetails", {}).get("departureStop", {})
        arr_stop = td.get("stopDetails", {}).get("arrivalStop", {})
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
        daily_cost_gbp = Money(str(round(total_bus_cost, 2)), "GBP")
    else:
        daily_cost_gbp = None

    return Commute(
        destination_label="Lorena — Aldgate / City of London (Bus)",
        destination_postcode=dest,
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

    label = dest_postcode
    no_bus = await TransitRoute(origin_postcode, dest_postcode, label, park_and_ride=has_car).plan()

    # When the traveler has a car, park-and-ride is preferred over bus.
    # If no_bus succeeded, return it directly.  If it failed, fall through
    # to try with_bus as a last resort.
    if has_car and not no_bus.impossible:
        return no_bus.value_or_none()

    with_bus = await TransitRoute(origin_postcode, dest_postcode, label, park_and_ride=has_car, allow_bus=True).plan()

    if no_bus.impossible and with_bus.impossible:
        return None

    empty = Commute(destination_label=label, destination_postcode=dest_postcode)
    no_bus_val = no_bus.value_or(empty)
    with_bus_val = with_bus.value_or(empty)
    result = _pick_best_route(no_bus_val, with_bus_val)

    # Bus fallback: if the chosen route has a long walk to the first
    # transit leg, try Google Routes transit as an alternative.
    # The bus only replaces the walking leg — the TfL route stays the same.
    if result is no_bus_val and no_bus_val.duration_minutes is not None:
        m = re.search(r"walk.*?\((\d+)m\)", no_bus_val.summary()[:60])
        walk_to_station = int(m.group(1)) if m else 0
        if walk_to_station >= settings.bus_walk_penalty_minutes:
            result = await _replace_walk_with_bus(
                tfl_commute=result,
                origin_postcode=origin_postcode,
                dest_postcode=dest_postcode,
                walk_to_station_minutes=walk_to_station,
            )

    return result


_SENTINEL = object()  # sentinel for _bus_alternative default


async def _replace_walk_with_bus(
    tfl_commute: Commute,
    origin_postcode: str,
    dest_postcode: str,
    walk_to_station_minutes: int,
    _bus_alternative: Commute | None | object = _SENTINEL,
) -> Commute:
    """Replace the walking leg of a TfL commute with a bus, if viable.

    The bus only replaces the walk *to the first transit stop* — the rest
    of the TfL route (train/tube legs) stays the same. This means the
    total cost is *TfL cost + bus cost*, and the total time is
    *TfL duration − walk + bus_time*.

    Returns the original commute unchanged when:
    * ``walk_to_station_minutes < bus_walk_penalty_minutes`` (walk is acceptable)
    * no bus alternative is available
    * the bus has no cost
    * the time savings don't justify the bus detour

    ``_bus_alternative`` — optional pre-resolved bus route (for test injection).
    When omitted, the function calls ``_find_bus_alternative()``.
    """
    penalty = settings.bus_walk_penalty_minutes
    if walk_to_station_minutes < penalty:
        return tfl_commute

    if _bus_alternative is _SENTINEL:
        bus = await _find_bus_alternative(origin_postcode, dest_postcode)
    else:
        bus = _bus_alternative
    if bus is None or bus.non_rail_cost() is None or bus.non_rail_cost() <= 0:
        return tfl_commute

    bus_cost = bus.non_rail_cost()
    bus_time = min(15, walk_to_station_minutes - penalty)
    savings = walk_to_station_minutes - bus_time
    if savings < penalty:
        return tfl_commute

    new_duration = tfl_commute.duration_minutes - walk_to_station_minutes + bus_time
    new_daily_cost = tfl_commute.daily_cost_gbp
    if new_daily_cost is not None:
        new_daily_cost = new_daily_cost + Money(str(bus_cost), "GBP")
    else:
        new_daily_cost = Money(str(bus_cost), "GBP")

    return Commute(
        destination_label=tfl_commute.destination_label,
        destination_postcode=tfl_commute.destination_postcode,
        duration_minutes=new_duration,
        daily_cost_gbp=new_daily_cost,
        mode=tfl_commute.mode,
        cost_groups=tfl_commute.cost_groups
        + (
            CostGroup(
                legs=(JourneyLeg(mode=LegMode.BUS, duration_minutes=bus_time),),
                cost=bus_cost,
            ),
        ),
    )


async def _find_bus_alternative(origin: str, dest: str) -> Commute | None:
    """Find a bus alternative via Google Routes API (for areas outside TfL coverage)."""
    body = {
        "origin": {"address": origin},
        "destination": {"address": dest},
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
    total_bus_cost = 0.0
    bus_cost_gbp = None
    for s in steps:
        if s.get("travelMode") != "TRANSIT":
            continue
        td = s.get("transitDetails", {})
        if td.get("transitLine", {}).get("vehicle", {}).get("type") != "BUS":
            continue
        dep_stop = td.get("stopDetails", {}).get("departureStop", {})
        arr_stop = td.get("stopDetails", {}).get("arrivalStop", {})
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
        daily_cost_gbp = Money(str(round(total_bus_cost, 2)), "GBP")
    else:
        daily_cost_gbp = None
    return Commute(
        destination_label="Lorena — Aldgate / City of London (Bus)",
        destination_postcode=dest,
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


def _address_waypoint(loc: str | GeoPoint) -> dict:
    """Build a Google Routes waypoint from a postcode string or GeoPoint."""
    if isinstance(loc, GeoPoint):
        return {"location": {"latLng": {"latitude": loc.lat, "longitude": loc.lon}}}
    return {"address": loc}


async def get_commute(
    origin_postcode: str | GeoPoint,
    dest_postcode: str | GeoPoint,
    *,
    has_car: bool,
    max_walk_minutes: int,
) -> Attempt[Commute]:
    """Route from origin to destination based on the traveler's circumstances.

    Parameters:
        origin_postcode: Where the traveler starts (postcode string or GeoPoint).
        dest_postcode: Where the traveler is going.
        has_car: Whether the traveler has access to a car.
        max_walk_minutes: Maximum acceptable walking time for the first/last
            leg. Beyond this, transit or driving is preferred.

    Returns an ``Attempt[Commute]``.  When no route is available or a backend
    fails, the attempt carries the source and reason (e.g. ``"google_routes"``,
    ``"API rate limited (429)"``).
    """
    candidates: list[Commute] = []
    failures: list[str] = []

    dest_str = dest_postcode if isinstance(dest_postcode, str) else f"{dest_postcode.lat},{dest_postcode.lon}"

    # ── 0. Congestion zone — skip driving ──────────────────────────
    dest_in_congestion = _in_congestion_zone(dest_str)

    # ── 1. Walking (cheapest to try) ───────────────────────────────
    try:
        walk = await _google_route_commute(origin_postcode, dest_postcode, "WALK", max_walk_minutes)
    except ValueError as e:
        failures.append(f"walk: {e}")
        walk = None
    if walk is not None and walk.duration_minutes is not None and walk.duration_minutes <= max_walk_minutes:
        return Attempt.succeeded(walk)
    if walk is not None:
        candidates.append(walk)

    # ── 2. Transit ─────────────────────────────────────────────────
    tfl: Commute | None = None
    origin_str = origin_postcode if isinstance(origin_postcode, str) else f"{origin_postcode.lat},{origin_postcode.lon}"

    if _is_london_area(dest_str):
        try:
            tfl = await _tfl_transit_commute(origin_str, dest_str, has_car)
        except Exception as e:
            logger.warning("TfL transit failed for %s → %s: %s", origin_str, dest_str, e)
            failures.append(f"tfl_transit: {e}")

    if tfl is not None:
        candidates.append(tfl)

    # ── 3. Driving ─────────────────────────────────────────────────
    if has_car and not dest_in_congestion:
        try:
            drive = await _google_route_commute(origin_postcode, dest_postcode, "DRIVE")
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
        # pricing for London including park-and-ride.  When we have both,
        # the priced result is more useful — the NR fare fallback
        # (applied later) can only approximate a rail fare and won't
        # capture bus or parking costs.
        def _tiebreak(c: Commute) -> tuple[int, float]:
            no_cost = 1 if (c.daily_cost_gbp is None or c.daily_cost_gbp == Money("0", "GBP")) else 0
            return (no_cost, c.duration_minutes or 0)

        return Attempt.succeeded(min(valid, key=_tiebreak))

    reason = "; ".join(failures) if failures else "no route available"
    return Attempt.impossible(reason)


def _with_label(commute: Commute, label: str, postcode: str) -> Commute:
    """Set destination label on a commute (Commute is frozen, so replace)."""
    import dataclasses

    return dataclasses.replace(commute, destination_label=label, destination_postcode=postcode)
