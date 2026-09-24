"""External API gateway — the ONE way to call a rate-limited external API.

Every external call in this codebase should go through :func:`api_fetch`.
It gives you, for free:

* **disk caching** (the existing ``with_cache`` rules: 2xx/3xx/404 cached,
  transient 4xx/5xx never),
* **per-API pacing** (a minimum interval between calls — ORS's free tier
  answers the daily quota with 403 and the per-minute limit with 429, so
  an unthrottled storm can burn a day of quota in minutes, 2026-09-24),
* **a daily-quota short-circuit** (the first quota-status response sets
  the flag; later calls raise ``DailyQuotaError`` WITHOUT a network hit
  until UTC midnight),
* **correct DAG classification** (``DailyQuotaError`` maps to a clear
  permanent "daily quota" attempt; 429/5xx stay transient for the DAG to
  retry).

Using it is "pick a profile constant, one call":

    data = await api_fetch(
        "POST", ORS_DIRECTIONS_URL, api=ORS,
        body=payload,
        headers={"Authorization": settings.ors_api_key, "Content-Type": "application/json"},
    )

Adding a NEW API = one ``ApiProfile(...)`` constant below. No pacing,
flag, cache or classification code of your own. To change an API's
behavior (interval, quota statuses), change ITS constant — every caller
follows.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from houses.api_cache import cached_async_client, with_cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApiProfile:
    """One external API's pacing and quota semantics.

    ``pace_s``           minimum seconds between calls (per process).
    ``quota_statuses``   HTTP statuses that mean "the KEY's quota is gone
                         for the day" — the first one sets the exhausted
                         flag and raises DailyQuotaError. ORS: 403 =
                         daily limit, 429 = per-minute (transient).
    ``timeout_s``        the httpx timeout for this API's calls.
    """

    pace_s: float
    quota_statuses: tuple[int, ...] = ()
    timeout_s: float = 15.0

    def is_quota(self, status: int) -> bool:
        return status in self.quota_statuses


# The profiles — each is a module-level constant, referenced by callers
# as ``api=apigw.ORS``. Adding an API = adding one constant here.

# ORS openrouteservice free tier (directions + geocode share the key: the
# DAILY quota is shared, 403; the per-minute limit is 429, transient).
ORS = ApiProfile(pace_s=0.25, quota_statuses=(403,))

# Nominatim — 1 req/s hard policy limit.
NOMINATIM = ApiProfile(pace_s=1.0)

# Google Maps/Places/Routes — GCP key quotas; both 403 and 429 can be
# either quota or auth, and GCP quotas recover within the day, so the
# exhausted flag is not useful — pace only, transient stays transient.
GOOGLE = ApiProfile(pace_s=0.25)

# postcodes.io — free, 1 req/s policy.
POSTCODESIO = ApiProfile(pace_s=1.0)

# TfL — free, generous; a light pace for politeness.
TFL = ApiProfile(pace_s=0.1)

# UK government EPC register — free, light pace.
GOV_EPC = ApiProfile(pace_s=0.5)

# OpenStreetMap Overpass — free; a light pace for politeness.
OVERPASS = ApiProfile(pace_s=0.5)


def _as_dict(obj: Any):
    """WirePayload dataclass or plain dict — the class's ``to_dict`` is the
    single serializer (the default WirePayload one handles plain dataclasses);
    callers never hand-build wire dicts in API classes."""
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


class DailyQuotaError(Exception):
    """The API KEY's daily quota is gone (per its ApiProfile).

    Permanent: retrying before UTC midnight is futile and only burns more
    quota — the DAG classifier maps this to a clear "daily quota" attempt.
    """


# ── per-process state (the quota belongs to the KEY, so it is
# ── process-wide, not per-request) ────────────────────────────────────
class ApiGate:
    """Process-wide pacing + daily-quota state, keyed by ApiProfile.

    One instance for the whole app: the KEY's quota and the pacing
    interval are process facts, not per-request ones. Clear/reset methods
    exist for tests."""

    def __init__(self) -> None:
        self._last_call: dict[ApiProfile, float] = {}
        self._exhausted_until: dict[ApiProfile, float] = {}

    @staticmethod
    def _next_utc_midnight() -> float:
        return (datetime.now(UTC) + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()

    def quota_exhausted(self, api: ApiProfile) -> bool:
        return time.time() < self._exhausted_until.get(api, 0.0)

    async def pace(self, api: ApiProfile) -> None:
        now = time.monotonic()
        last = self._last_call.get(api, 0.0)
        if last and now - last < api.pace_s:
            await asyncio.sleep(api.pace_s - (now - last))
        self._last_call[api] = time.monotonic()

    def mark_quota_exhausted(self, api: ApiProfile) -> None:
        if time.time() < self._exhausted_until.get(api, 0.0):
            return
        self._exhausted_until[api] = self._next_utc_midnight()
        logger.warning(
            "%s: daily quota exhausted — calls short-circuited until UTC midnight", api
        )

    def clear_quota(self, api: ApiProfile | None = None) -> None:
        if api is None:
            self._exhausted_until.clear()
        else:
            self._exhausted_until.pop(api, None)

    def reset_pacing(self, api: ApiProfile | None = None) -> None:
        if api is None:
            self._last_call.clear()
        else:
            self._last_call.pop(api, None)


GATE = ApiGate()


# lucidlint: ignore long-param-list the flat signature IS the ease-of-use
# contract — every caller would otherwise build a parameter object per call
async def api_fetch(
    method: str,
    url: str,
    *,
    api: ApiProfile,
    params: Any = None,
    body: Any = None,
    headers: dict[str, str] | None = None,
    wire_params: dict[str, str] | None = None,
    _client_factory=None,
) -> Any:
    """Fetch a guarded external API call: cache, pace, quota, classify.

    Return the JSON body, or raise a typed error: ``DailyQuotaError``
    (permanent) or the original ``httpx`` error (429/5xx stay transient
    for the DAG; other statuses are permanent). Not JSON? Use
    ``with_cache`` directly and call ``_pace``/``mark_quota_exhausted``
    yourself — the guard helpers are still one line each.
    """
    if GATE.quota_exhausted(api):
        raise DailyQuotaError(f"{api}: quota exhausted")
    await GATE.pace(api)

    async def _fetch() -> Any:
        factory = _client_factory or cached_async_client
        async with factory(timeout=api.timeout_s) as client:
            try:
                resp = await client.request(
                    method,
                    url,
                    params={**(_as_dict(params) or {}), **(wire_params or {})},
                    json=_as_dict(body),
                    headers=headers,
                )
            except httpx.HTTPStatusError as e:
                # the raised path: a quota status inside the error flips the
                # flag too (raise_for_status-style clients and test fakes)
                if api.is_quota(e.response.status_code):
                    GATE.mark_quota_exhausted(api)
                    raise DailyQuotaError(
                        f"{api}: HTTP {e.response.status_code} (daily quota) for {url}"
                    ) from e
                raise
            if api.is_quota(resp.status_code):
                GATE.mark_quota_exhausted(api)
                raise DailyQuotaError(f"{api}: HTTP {resp.status_code} (daily quota) for {url}")
            resp.raise_for_status()
            return resp.json()

    return await with_cache(method, url, params=params, body=body, fetch=_fetch)