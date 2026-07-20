"""Commute routing — unified interface for walking, transit, and driving.

The caller describes the traveler; ``get_commute`` handles the rest.
No knowledge of Google, TfL, or ORS leaks to callers.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from houses.api_cache import cached_async_client, get_cached, set_cached
from houses.bus_fare_reader import get_bus_fare_reader
from houses.bus_journey import cheapest_round_trip
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.config import settings
from houses.geo import GeoPoint
from houses.http_error import HttpError
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.transit_route import TransitRoute

logger = logging.getLogger(__name__)


def _bus_fare_for(
    dep_name: str,
    arr_name: str,
    dep_point: dict[str, float] | None = None,
    arr_point: dict[str, float] | None = None,
) -> Money | None:
    """Look up daily round-trip bus cost between two stops.

    Delegates to ``BusJourneyRegistry`` which handles direct name match,
    fuzzy token match, and coordinate-based match (100m radius via spatial
    index).  No expanding-radius search — the point of taking the bus is
    to avoid long walks.

    Returns the cost as Money, or ``None`` if no fare is found.
    """
    fares_r = get_bus_fare_reader()
    fares = fares_r.fares_for_stops(dep_name, arr_name, dep_point=dep_point, arr_point=arr_point)
    cheapest = cheapest_round_trip(fares, fares_r.national_max_single)
    if cheapest is not None:
        return Money(str(round(cheapest.amount, 2)), "GBP")
    return None


# ---------------------------------------------------------------------------
# API URLs
# ---------------------------------------------------------------------------

GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"




def _raise_with_body(resp: httpx.Response) -> None:
    """Call ``raise_for_status()`` with the response body appended.

    httpx.HTTPStatusError.__str__() only includes the status code and
    URL, not the response body.  When Google Routes rejects a request
    (e.g. 400 with 'LatLng cannot be specified as an Address Waypoint'),
    the body is the only place the reason appears.
    """
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        body = resp.text[:1000]
        raise httpx.HTTPStatusError(
            f"{e} — {body}", request=e.request, response=e.response
        ) from e

async def _google_routes_post(
    body: dict,
    field_mask: str,
    *,
    timeout: float = 10.0,
) -> dict | None:
    """POST to Google Routes API, caching responses and direct HTTP call.

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

    async with cached_async_client(timeout=timeout) as client:
        resp = await client.post(GOOGLE_ROUTES_URL, json=body, headers=headers)
        if resp.status_code == 429:
            raise HttpError(429, "rate limited", headers=dict(resp.headers))
        _raise_with_body(resp)
        data = resp.json()
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
    if oc:
        return oc in _CONGESTION_OUTCODES
    # Check coordinate strings ("lat,lon") — central London congestion zone box
    if "," in postcode:
        try:
            lat, lon = postcode.split(",")
            return 51.5 < float(lat) < 51.52 and -0.15 < float(lon) < 0.01
        except (ValueError, TypeError):
            pass
    return False


def _is_london_area(postcode: str) -> bool:
    """Rough check: is this postcode in the TfL service area?

    This is an OPTIMISATION, not a correctness gate.  A false positive
    (trying TfL for an out-of-area destination) is harmless — the API
    returns no routes and the caller falls through to driving.  A false
    negative means we skip TfL for a London destination, still getting
    a valid driving result.
    """
    oc = _outcode_from_postcode(postcode)
    if oc:
        return oc.startswith(("E", "EC", "N", "NW", "SE", "SW", "W", "WC"))
    # Check coordinate strings ("lat,lon") — London bounding box
    if "," in postcode:
        try:
            lat, lon = postcode.split(",")
            return 51.3 < float(lat) < 51.7 and -0.5 < float(lon) < 0.3  # approx Greater London
        except (ValueError, TypeError):
            pass
    return False


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


async def _google_route_commute(
    origin: str | GeoPoint, dest: str | GeoPoint, mode: str, max_walk_minutes: int | None = None
) -> Commute | None:
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
    mask = "routes.duration,routes.distanceMeters,routes.legs"
    data = await _google_routes_post(body, mask, timeout=15.0 if mode == "DRIVE" else 10.0)
    if data is None:
        return None

    routes = data.get("routes", [])
    if not routes:
        return None
    duration_sec = int(routes[0].get("duration", "0s").rstrip("s"))
    duration_min = round(duration_sec / 60)
    distance_meters = routes[0].get("distanceMeters", 0)

    dest_str = dest if isinstance(dest, str) else f"{dest.lat},{dest.lon}"
    if mode == "WALK":
        leg = JourneyLeg(mode=LegMode.WALK, duration_minutes=duration_min)
        daily = Money("0", "GBP")
    else:
        distance_km = distance_meters / 1000
        leg = JourneyLeg(mode=LegMode.DRIVE, duration_minutes=duration_min, distance_km=distance_km)
        daily = Money("0", "GBP")
    return Commute(
        person=Person(name="", has_car=False),
        label="",
        destination=PlaceOfInterest(label="", postcode=dest_str),
        duration=Quantity(duration_min, "minute"),
        daily_cost=daily or Money("0", "GBP"),
        mode="walk" if mode == "WALK" else "drive",
        details=(
            CostGroup(
                legs=(leg,),
                cost=daily,
            ),
        ),
    )


def _first_walk_minutes(commute: Commute) -> int:
    """Return the duration (minutes) of the first walk leg in a commute, or 0."""
    for cg in commute.details:
        for leg in cg.legs:
            if leg.mode == LegMode.WALK:
                return leg.duration_minutes
    return 0


# ---------------------------------------------------------------------------
# Transit — TfL via TransitRoute (London area)
# ---------------------------------------------------------------------------


async def _tfl_transit_commute(origin_postcode: str, dest_postcode: str, has_car: bool) -> Commute | None:
    """Transit routing via TfL API.

    Tries routes with and without bus mode, picks the best.
    Also applies bus fallback via Google Routes when the first-leg
    walk exceeds max_walk_minutes (TfL doesn't cover some areas).
    """
    label = dest_postcode
    no_bus = await TransitRoute(
        origin_postcode,
        dest_postcode,
        label,
        park_and_ride=has_car,
        fare_lookup=_bus_fare_for,
    ).plan()

    # When the traveler has a car, park-and-ride is preferred over bus.
    # If no_bus succeeded, return it directly.  If it failed, fall through
    # to try with_bus as a last resort.
    if has_car and not no_bus.impossible:
        return no_bus.value_or_none()

    with_bus = await TransitRoute(origin_postcode, dest_postcode, label, park_and_ride=has_car, allow_bus=True).plan()

    if no_bus.impossible and with_bus.impossible:
        return None

    empty = Commute(
        person=Person(name="", has_car=has_car),
        label=label,
        destination=PlaceOfInterest(label=label, postcode=dest_postcode),
        duration=Quantity(0, "minute"),
        daily_cost=Money("0", "GBP"),
    )
    no_bus_val = no_bus.value_or(empty)
    with_bus_val = with_bus.value_or(empty)
    result = _pick_best_route(no_bus_val, with_bus_val)

    # Bus fallback: if the chosen route has a long walk to the first
    # transit leg, try Google Routes transit as an alternative.
    # The bus only replaces the walking leg — the TfL route stays the same.
    if result is no_bus_val and result.duration.magnitude > 0:
        walk_to_station = _first_walk_minutes(result)
        if walk_to_station >= settings.bus_walk_penalty_minutes:
            result = await _replace_walk_with_bus(
                tfl_commute=result,
                origin_postcode=origin_postcode,
                dest_postcode=dest_postcode,
                walk_to_station_minutes=walk_to_station,
                has_car=has_car,
            )

    return result


_SENTINEL = object()  # sentinel for _bus_alternative default


async def _replace_walk_with_bus(
    tfl_commute: Commute,
    origin_postcode: str,
    dest_postcode: str,
    walk_to_station_minutes: int,
    has_car: bool = False,
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
        bus = await _find_bus_alternative(origin_postcode, dest_postcode, has_car=has_car)
    else:
        bus = _bus_alternative
    if bus is None:
        return tfl_commute

    # Inline non_rail_cost: sum non-rail costs from commute details
    bus_costs: list[float] = []
    for cg in bus.details:
        if cg.cost is not None:
            if isinstance(cg.cost, Money):
                bus_costs.append(float(cg.cost.amount))
            else:
                bus_costs.append(float(cg.cost))
    bus_cost = sum(bus_costs) if bus_costs else None

    if bus_cost is None or bus_cost <= 0:
        return tfl_commute

    bus_time = min(15, walk_to_station_minutes - penalty)
    savings = walk_to_station_minutes - bus_time
    if savings < penalty:
        return tfl_commute

    new_duration = int(tfl_commute.duration.magnitude - walk_to_station_minutes + bus_time)
    new_daily_cost = tfl_commute.daily_cost + Money(str(bus_cost), "GBP")

    return Commute(
        person=tfl_commute.person,
        label=tfl_commute.label,
        destination=tfl_commute.destination,
        duration=Quantity(new_duration, "minute"),
        daily_cost=new_daily_cost,
        mode=tfl_commute.mode,
        details=tfl_commute.details
        + (
            CostGroup(
                legs=(JourneyLeg(mode=LegMode.BUS, duration_minutes=bus_time),),
                cost=bus_cost,
            ),
        ),
    )


async def _find_bus_alternative(origin: str, dest: str, has_car: bool = False) -> Commute | None:
    """Find a bus alternative via Google Routes API (for areas outside TfL coverage)."""
    body = {
        "origin": _address_waypoint(origin),
        "destination": _address_waypoint(dest),
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
    steps = leg.get("steps", [])
    duration_sec = int(routes[0].get("duration", "0s").rstrip("s"))
    duration_min = round(duration_sec / 60)
    total_bus_cost = Money("0", "GBP")
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
    daily_cost_gbp = total_bus_cost if total_bus_cost > Money("0", "GBP") else None
    return Commute(
        person=Person(name="", has_car=has_car),
        label="Lorena — Aldgate / City of London (Bus)",
        destination=PlaceOfInterest(
            label="Lorena — Aldgate / City of London (Bus)",
            postcode=dest,
        ),
        duration=Quantity(duration_min, "minute"),
        daily_cost=daily_cost_gbp if daily_cost_gbp is not None else Money("0", "GBP"),
        mode="transit",
        details=(
            CostGroup(
                legs=(JourneyLeg(mode=LegMode.BUS, duration_minutes=duration_min or 0),),
                cost=daily_cost_gbp,
            ),
        )
        if daily_cost_gbp is not None
        else (),
    )


def _pick_best_route(a: Commute, b: Commute) -> Commute:
    """Pick the better of two transit route options.

    Uses the ``b`` result only if it saves at least
    ``bus_walk_penalty_minutes`` over ``a``.
    """
    if b.duration.magnitude == 0:
        return a
    if a.duration.magnitude == 0:
        return b
    savings = a.duration.magnitude - b.duration.magnitude
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
    # If the string looks like "lat,lon", parse it as a location waypoint
    if "," in loc:
        try:
            lat, lon = loc.split(",", 1)
            return {"location": {"latLng": {"latitude": float(lat), "longitude": float(lon)}}}
        except (ValueError, TypeError):
            pass
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
    if walk is not None and walk.duration.magnitude <= max_walk_minutes:
        return Attempt.succeeded(walk)
    if walk is not None:
        candidates.append(walk)

    # ── 2. Transit ─────────────────────────────────────────────────
    tfl: Commute | None = None
    origin_str = origin_postcode if isinstance(origin_postcode, str) else f"{origin_postcode.lat},{origin_postcode.lon}"

    if _is_london_area(dest_str):
        try:
            tfl = await _tfl_transit_commute(origin_str, dest_str, has_car)
        except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException, HttpError):
            raise
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
    valid = [c for c in candidates if c.duration.magnitude > 0]
    if valid:
        # Prefer priced routes over faster non-priced ones.
        # Priority:
        #   1. Has real cost data (non-zero daily_cost)
        #   2. Faster duration
        # Google Routes may return the fastest transit option but often
        # lacks bus/rail fare data (cost=0).  TfL has accurate
        # pricing for London including park-and-ride.  When we have both,
        # the priced result is more useful — the NR fare fallback
        # (applied later) can only approximate a rail fare and won't
        # capture bus or parking costs.
        def _tiebreak(c: Commute) -> tuple[int, float]:
            no_cost = 1 if c.daily_cost == Money("0", "GBP") else 0
            return (no_cost, c.duration.magnitude or 0)

        return Attempt.succeeded(min(valid, key=_tiebreak))

    reason = "; ".join(failures) if failures else "no route available"
    return Attempt.impossible(reason)


def _with_label(commute: Commute, label: str, postcode: str) -> Commute:
    """Set destination label on a commute (Commute is frozen, so replace)."""
    import dataclasses

    return dataclasses.replace(
        commute,
        label=label,
        destination=PlaceOfInterest(label=label, postcode=postcode),
    )
