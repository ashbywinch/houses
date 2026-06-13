"""Commute computation, petrol cost, and commute breakdown logic.

Delegates transit routing to ``houses.routing.get_commute`` and
driving routes to ``houses.routing._drive_commute``.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from houses.api_cache import get_cached, set_cached
from houses.attempt import Attempt
from houses.commute import Commute, CommuteBreakdown
from houses.config import settings
from houses.retry import retry_async

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API response cache helpers
# ---------------------------------------------------------------------------


async def _cached_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    *,
    max_retries: int = 0,
) -> dict | None:
    """GET with disk-backed JSON caching. Returns the parsed JSON or ``None``."""
    cached = get_cached("GET", url, params)
    if cached is not None:
        return cached

    async def _fetch():
        return await client.get(url, params=params)

    resp = await retry_async(_fetch, max_retries=max_retries, base_delay=0.5) if max_retries else await _fetch()
    resp.raise_for_status()
    data = resp.json()
    set_cached("GET", url, params, None, data)
    return data


async def _cached_post(
    client: httpx.AsyncClient,
    url: str,
    json_body: dict | None = None,
    headers: dict | None = None,
    *,
    max_retries: int = 0,
) -> dict | None:
    """POST with disk-backed JSON caching. Returns the parsed JSON or ``None``."""
    body_str = json.dumps(json_body, sort_keys=True) if json_body else None
    cached = get_cached("POST", url, None, body_str)
    if cached is not None:
        return cached

    async def _fetch():
        return await client.post(url, json=json_body, headers=headers)

    resp = await retry_async(_fetch, max_retries=max_retries, base_delay=0.5) if max_retries else await _fetch()
    resp.raise_for_status()
    data = resp.json()
    set_cached("POST", url, None, body_str, data)
    return data


# Full postcode: "SL6 1AA", outcode: "SL6"
_OUTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")
# Trailing postcode in address strings (e.g. ", SL6" or ", GU22 8BQ")
_END_PC_RE = re.compile(r",\s*[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?\s*$", re.IGNORECASE)


async def compute_simon_commute(property_postcode: str) -> Attempt[Commute]:
    from houses.routing import _with_label, get_commute

    result = await get_commute(property_postcode, settings.simon_postcode, has_car=True, max_walk_minutes=15)
    if result.is_succeeded:
        commute = result.value_or_none()
        return Attempt.succeeded(
            _with_label(commute, "Simon — Pimlico / Victoria", settings.simon_postcode),
            result.source,
        )
    # Propagate the failure reason from get_commute
    return Attempt.impossible(result.source, result.reason)


async def compute_lorena_commute(property_postcode: str) -> Attempt[Commute]:
    from houses.routing import _with_label, get_commute

    result = await get_commute(property_postcode, settings.lorena_postcode, has_car=False, max_walk_minutes=30)
    if result.is_succeeded:
        commute = result.value_or_none()
        return Attempt.succeeded(
            _with_label(commute, "Lorena — Aldgate / City of London", settings.lorena_postcode),
            result.source,
        )
    return Attempt.impossible(result.source, result.reason)


def _pick_best_lorena_route(no_bus: Commute, with_bus: Commute) -> Commute:
    """Compare no-bus and with-bus commute results.

    Uses the with-bus result only if it saves at least 15 minutes
    over the no-bus first-leg walk.
    """
    if with_bus.duration_minutes is None:
        return no_bus
    if no_bus.duration_minutes is None:
        return with_bus

    no_bus_saves = no_bus.duration_minutes - with_bus.duration_minutes
    if no_bus_saves >= settings.bus_walk_penalty_minutes:
        return with_bus
    return no_bus


async def compute_commute_breakdown(
    simon_transit: Commute,
    lorena_transit: Commute,
    bracknell: Commute,
) -> CommuteBreakdown:
    # Convert Money → float for CommuteBreakdown compatibility
    simon_daily = float(simon_transit.daily_cost_gbp.amount) if simon_transit.daily_cost_gbp is not None else None
    lorena_daily = float(lorena_transit.daily_cost_gbp.amount) if lorena_transit.daily_cost_gbp is not None else None
    bracknell_daily = float(bracknell.daily_cost_gbp.amount) if bracknell.daily_cost_gbp is not None else None

    yearly_total = None
    formula = ""

    if simon_daily is not None and lorena_daily is not None and bracknell_daily is not None:
        yearly_total = round(
            float(
                settings.working_weeks_per_year
                * (bracknell_daily + simon_daily + lorena_daily * settings.weekly_lorena_trips)
            ),
            2,
        )
        formula = (
            f"{settings.working_weeks_per_year}wk x "
            f"({settings.weekly_bracknell_trips}xBracknell_daily + "
            f"{settings.weekly_lorena_trips}xLorena_daily + "
            f"{settings.weekly_simon_trips}xSimon_daily)"
        )

    return CommuteBreakdown(
        simon_daily_gbp=simon_daily,
        lorena_daily_gbp=lorena_daily,
        bracknell_daily_gbp=bracknell_daily,
        yearly_total_gbp=yearly_total,
        formula_explanation=formula,
    )


# ---------------------------------------------------------------------------
# Petrol — OpenRouteService driving distance
# ---------------------------------------------------------------------------


def _compute_petrol_from_distance_km(round_trip_km: float) -> float:
    litres_per_100km = 235.214 / settings.petrol_mpg
    litres_used = (round_trip_km / 100) * litres_per_100km
    return round(litres_used * settings.petrol_price_per_litre, 2)


async def compute_petrol_cost(origin_postcode: str) -> Attempt[Commute]:
    """Bracknell commute — driving cost via ORS.

    Note: This still exists as a separate function because the sheet
    always shows a Bracknell cost, even when transit might be faster.
    The ``get_commute`` function (in routing.py) handles the
    transit-vs-driving comparison for other callers.
    """
    from houses.routing import _drive_commute, _with_label

    commute = await _drive_commute(origin_postcode, settings.bracknell_postcode)
    if commute:
        return Attempt.succeeded(
            _with_label(commute, "Bracknell Office (RG12 8YA)", settings.bracknell_postcode),
            "ors",
        )
    return Attempt.impossible("petrol", "could not route to Bracknell")
