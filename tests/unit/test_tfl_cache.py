"""Cache-semantics tests — error responses are never cached or served.

Covers the behaviours changed for the never-cache-non-OK standard:
- the transport evicts legacy wrapped-error entries on the hit path
- _cached_api_call rejects raw ApiError bodies on the hit path
- _cached_with_retry retries transient errors, stops on non-transient,
  returns None when attempts are exhausted

Note: the transport keys with the percent-encoded request URL
(``SW1V%202QQ``) while ``_cached_api_call``'s direct lookups use the raw
space form — two key spaces; tests seed whichever space the code under test
reads.
"""

from __future__ import annotations

import httpx
import pytest

from dag.http_error import HttpError
from houses.api_cache import CachingTransport, get_cached, set_cache_dir, set_cached
from houses.tfl_client import TflClient

URL = "https://api.tfl.gov.uk/Journey/JourneyResults/51.5,-0.1/to/SW1V 2QQ"
ENCODED_URL = "https://api.tfl.gov.uk/Journey/JourneyResults/51.5,-0.1/to/SW1V%202QQ"
AUTH_PARAMS = {"nationalSearch": "true", "app_key": "test-key"}
STRIPPED_PARAMS = {"nationalSearch": "true"}


class _FakeInner(httpx.AsyncBaseTransport):
    """Records requests; returns a canned response — no network."""

    def __init__(self, response: httpx.Response):
        self.response = response
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


# ── transport: legacy wrapped-error entries ──────────────────────────


@pytest.mark.asyncio
async def test_transport_evicts_wrapped_error_on_hit(tmp_path):
    set_cache_dir(tmp_path)
    # A legacy wrapped-error entry, keyed as the transport keys (encoded URL,
    # auth params from the query string).
    set_cached("GET", ENCODED_URL, AUTH_PARAMS, None, {"_cached_status": 429, "_cached_body": {"error": "x"}})

    inner = _FakeInner(httpx.Response(200, json={"journeys": [{"duration": 5}]}))
    async with httpx.AsyncClient(transport=CachingTransport(inner=inner)) as client:
        resp = await client.get(URL, params=AUTH_PARAMS)

    assert resp.status_code == 200
    assert resp.json()["journeys"][0]["duration"] == 5  # served from the inner, not the poison
    assert inner.requests  # the cached error did not short-circuit the request
    # The poison was evicted — the slot now holds the fresh success, not the 429.
    entry = get_cached("GET", ENCODED_URL, AUTH_PARAMS, None)
    assert entry is None or "_cached_status" not in entry


@pytest.mark.asyncio
async def test_transport_serves_wrapped_non_error_entries(tmp_path):
    set_cache_dir(tmp_path)
    set_cached("GET", ENCODED_URL, AUTH_PARAMS, None, {"_cached_status": 300, "_cached_body": {"disambiguation": []}})
    inner = _FakeInner(httpx.Response(500, json={}))
    async with httpx.AsyncClient(transport=CachingTransport(inner=inner)) as client:
        resp = await client.get(URL, params=AUTH_PARAMS)
    assert resp.status_code == 300  # 3xx wrapped entries are still served (disambiguation)
    assert not inner.requests


# ── _cached_api_call: raw ApiError bodies on the hit path ────────────


@pytest.mark.asyncio
async def test_cached_api_call_rejects_raw_error_body(tmp_path):
    set_cache_dir(tmp_path)
    # Poisoned entry under the STRIPPED-key/raw-URL space (old _cached_api_call
    # writes)…
    set_cached(
        "GET",
        URL,
        STRIPPED_PARAMS,
        None,
        {"$type": "Tfl.Api.Presentation.Entities.ApiError, Tfl.Api.Presentation.Entities", "message": "boom"},
    )
    # …and a good entry under the transport's encoded-URL/auth-keyed variant,
    # so the post-eviction request is served from cache (no network).
    set_cached("GET", ENCODED_URL, AUTH_PARAMS, None, {"journeys": [{"duration": 9}]})

    data = await TflClient._cached_api_call(URL, AUTH_PARAMS)

    assert data is not None
    assert data["journeys"][0]["duration"] == 9  # fresh data, not the error
    # The poison was evicted and replaced by the good response in the slot.
    entry = get_cached("GET", URL, STRIPPED_PARAMS, None)
    assert entry is None or "ApiError" not in str(entry.get("$type", ""))


# ── _cached_with_retry: retry semantics ──────────────────────────────


@pytest.mark.asyncio
async def test_retry_transient_then_succeeds():
    calls: list[int] = []

    async def fetch(_url, _params):
        calls.append(1)
        if len(calls) < 3:
            raise HttpError(429)
        return {"journeys": [{"duration": 7}]}

    data = await TflClient._cached_with_retry(URL, AUTH_PARAMS, attempts=3, base_delay=0, fetch=fetch)
    assert data is not None
    assert data["journeys"][0]["duration"] == 7
    assert len(calls) == 3  # two transient failures, then success


@pytest.mark.asyncio
async def test_retry_exhausted_returns_none():
    calls: list[int] = []

    async def fetch(_url, _params):
        calls.append(1)
        raise HttpError(429)

    assert await TflClient._cached_with_retry(URL, AUTH_PARAMS, attempts=3, base_delay=0, fetch=fetch) is None
    assert len(calls) == 3  # all attempts used


@pytest.mark.asyncio
async def test_retry_stops_immediately_on_non_transient():
    calls: list[int] = []

    async def fetch(_url, _params):
        calls.append(1)
        raise HttpError(400)

    assert await TflClient._cached_with_retry(URL, AUTH_PARAMS, attempts=3, base_delay=0, fetch=fetch) is None
    assert len(calls) == 1  # 4xx is not retried


@pytest.mark.asyncio
async def test_retry_recovers_after_network_error():
    calls: list[int] = []

    async def fetch(_url, _params):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.RequestError("boom")
        return {"journeys": [{"duration": 11}]}

    data = await TflClient._cached_with_retry(URL, AUTH_PARAMS, attempts=3, base_delay=0, fetch=fetch)
    assert data is not None
    assert data["journeys"][0]["duration"] == 11
    assert len(calls) == 2
