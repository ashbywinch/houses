"""Cache-semantics tests — transient errors are never cached or served.

Rule (refined): deterministic responses — 2xx/3xx/4xx, including 404
"cannot route this station" bodies — ARE cached (re-hitting the endpoint for
an impossible request wastes calls). TRANSIENT errors (429/5xx) are never
cached, and legacy cached transient entries are evicted on the hit path so
retries stay genuine.
"""

from __future__ import annotations

import json
from typing import override

import httpx
import pytest

from dag.http_error import HttpError
from houses.api_cache import CachingTransport, get_cached, set_cached
from houses.tfl_client import TflClient

URL = "https://api.tfl.gov.uk/Journey/JourneyResults/51.5,-0.1/to/SW1V 2QQ"
AUTH_PARAMS = {"nationalSearch": "true", "app_key": "test-key"}
STRIPPED_PARAMS = {"nationalSearch": "true"}


@pytest.fixture
async def isolated_cache(tmp_path):
    """Point the API cache at a temp dir and RESTORE the previous dir after.

    set_cache_dir mutates a module global; leaking it would make full-suite
    behaviour depend on collection order (later cache users read a deleted
    temp dir).
    """
    from houses import api_cache

    previous = api_cache.CACHE_DIR
    api_cache.set_cache_dir(tmp_path)
    yield tmp_path
    api_cache.set_cache_dir(previous)


class _FakeInner(httpx.AsyncBaseTransport):
    """Records requests; returns a canned response — no network."""

    def __init__(self, response: httpx.Response):
        self.response = response
        self.requests: list[httpx.Request] = []

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


# ── transport: legacy wrapped entries ───────────────────────────────


@pytest.mark.asyncio
async def test_transport_evicts_wrapped_transient_error_on_hit(isolated_cache):
    # A legacy wrapped 429 in the UNIFIED key space (raw URL, auth stripped).
    set_cached("GET", URL, STRIPPED_PARAMS, None, {"_cached_status": 429, "_cached_body": {"error": "x"}})

    inner = _FakeInner(httpx.Response(200, json={"journeys": [{"duration": 5}]}))
    async with httpx.AsyncClient(transport=CachingTransport(inner=inner)) as client:
        resp = await client.get(URL, params=AUTH_PARAMS)

    assert resp.status_code == 200
    assert resp.json()["journeys"][0]["duration"] == 5  # served from the inner, not the poison
    assert inner.requests  # the cached 429 did not short-circuit the request
    # The poison was evicted — the slot now holds the fresh success, not the 429.
    entry = get_cached("GET", URL, STRIPPED_PARAMS, None)
    assert entry is None or "_cached_status" not in entry


@pytest.mark.asyncio
async def test_transport_serves_wrapped_deterministic_404(isolated_cache):
    # A deterministic no-route response (wrapped 404) IS served — re-hitting
    # the endpoint for the same impossible request wastes calls.
    set_cached(
        "GET",
        URL,
        STRIPPED_PARAMS,
        None,
        {"_cached_status": 404, "_cached_body": {"$type": "ApiError", "message": "no route"}},
    )
    inner = _FakeInner(httpx.Response(500, json={}))
    async with httpx.AsyncClient(transport=CachingTransport(inner=inner)) as client:
        resp = await client.get(URL, params=AUTH_PARAMS)
    assert resp.status_code == 404  # served from cache; the inner was never hit
    assert not inner.requests


@pytest.mark.asyncio
async def test_transport_serves_wrapped_non_error_entries(isolated_cache):
    set_cached("GET", URL, STRIPPED_PARAMS, None, {"_cached_status": 300, "_cached_body": {"disambiguation": []}})
    inner = _FakeInner(httpx.Response(500, json={}))
    async with httpx.AsyncClient(transport=CachingTransport(inner=inner)) as client:
        resp = await client.get(URL, params=AUTH_PARAMS)
    assert resp.status_code == 300  # 3xx wrapped entries are still served (disambiguation)
    assert not inner.requests


# ── _cached_api_call: hit-path semantics ─────────────────────────────


@pytest.mark.asyncio
async def test_cached_api_call_evicts_transient_raw_error(isolated_cache):
    # Legacy raw 500 ApiError body in the unified key space — evicted, and the
    # post-eviction request served by a fake inner (hermetic, no network).
    set_cached(
        "GET",
        URL,
        STRIPPED_PARAMS,
        None,
        {"$type": "Tfl.Api.Presentation.Entities.ApiError, Tfl.Api.Presentation.Entities", "httpStatusCode": 500},
    )

    def client_factory(**kwargs):
        return httpx.AsyncClient(
            transport=CachingTransport(inner=_FakeInner(httpx.Response(200, json={"journeys": [{"duration": 9}]})))
        )

    data = await TflClient._cached_api_call(URL, AUTH_PARAMS, _client_factory=client_factory)

    assert data is not None
    assert data["journeys"][0]["duration"] == 9  # fresh data, not the poison
    entry = get_cached("GET", URL, STRIPPED_PARAMS, None)
    assert entry is None or "httpStatusCode" not in entry  # transient poison gone


@pytest.mark.asyncio
async def test_cached_api_call_serves_deterministic_404_body(isolated_cache):
    # A cached 404 "cannot route" body is a legit answer — served from cache,
    # no re-hit, no eviction.
    set_cached(
        "GET",
        URL,
        STRIPPED_PARAMS,
        None,
        {"$type": "Tfl.Api.Presentation.Entities.ApiError, Tfl.Api.Presentation.Entities", "httpStatusCode": 404},
    )
    data = await TflClient._cached_api_call(URL, AUTH_PARAMS)
    assert data is not None
    assert data.get("httpStatusCode") == 404  # the deterministic no-route answer
    assert get_cached("GET", URL, STRIPPED_PARAMS, None) is not None  # still cached


@pytest.mark.asyncio
async def test_cached_api_call_unwraps_wrapped_entry(isolated_cache):
    # New-format deterministic 4xx: wrapped with its status. _cached_api_call
    # returns the BODY, not the wrapper.
    set_cached(
        "GET",
        URL,
        STRIPPED_PARAMS,
        None,
        {"_cached_status": 404, "_cached_body": {"message": "no route"}},
    )
    data = await TflClient._cached_api_call(URL, AUTH_PARAMS)
    assert data == {"message": "no route"}


@pytest.mark.asyncio
async def test_cached_api_call_caches_404_wrapped_but_not_429(isolated_cache):
    # Write path: a live 404 is cached WRAPPED (status preserved); a live 429
    # is not (transient — the route must stay retriable). Distinct URLs so the
    # cached 404 does not short-circuit the 429 case.
    url_404 = f"{URL}/a"
    url_429 = f"{URL}/b"

    def client_404(**kwargs):
        return httpx.AsyncClient(
            transport=CachingTransport(inner=_FakeInner(httpx.Response(404, json={"message": "no route"})))
        )

    with pytest.raises(HttpError) as excinfo:
        await TflClient._cached_api_call(url_404, AUTH_PARAMS, _client_factory=client_404)
    assert excinfo.value.status == 404
    entry = get_cached("GET", url_404, STRIPPED_PARAMS, None)
    assert entry is not None
    assert entry["_cached_status"] == 404  # wrapped, status kept

    def client_429(**kwargs):
        return httpx.AsyncClient(
            transport=CachingTransport(inner=_FakeInner(httpx.Response(429, json={"message": "slow down"})))
        )

    with pytest.raises(HttpError) as excinfo:
        await TflClient._cached_api_call(url_429, AUTH_PARAMS, _client_factory=client_429)
    assert excinfo.value.status == 429
    assert get_cached("GET", url_429, STRIPPED_PARAMS, None) is None  # 429 NOT cached


@pytest.mark.asyncio
async def test_cached_api_call_live_404_has_friendly_user_message(isolated_cache):
    """A live TfL 404 must surface a friendly user_message — the UI renders
    display_message, and a raw ApiError body must never reach it."""
    body = {"$type": "Tfl.Api.Presentation.Entities.ApiError", "httpStatusCode": 404, "message": "no route"}
    inner = _FakeInner(httpx.Response(404, json=body))

    def client_factory(**kw):
        return httpx.AsyncClient(transport=CachingTransport(inner=inner))

    with pytest.raises(HttpError) as excinfo:
        await TflClient._cached_api_call(URL, AUTH_PARAMS, _client_factory=client_factory)
    e = excinfo.value
    assert e.status == 404
    assert e.user_message == "TfL couldn't find a route for this journey"
    assert "$type" not in e.user_message
    # The raw body stays in the internal message and body for logs.
    assert "$type" in str(e)
    assert "$type" in e.body


@pytest.mark.asyncio
async def test_cached_api_call_live_429_has_friendly_user_message(isolated_cache):
    inner = _FakeInner(httpx.Response(429, json={"error": "rate limit"}))

    def client_factory(**kw):
        return httpx.AsyncClient(transport=CachingTransport(inner=inner))

    with pytest.raises(HttpError) as excinfo:
        await TflClient._cached_api_call(f"{URL}/rl", AUTH_PARAMS, _client_factory=client_factory)
    e = excinfo.value
    assert e.status == 429
    assert e.user_message == "TfL is busy right now — try again shortly"


@pytest.mark.asyncio
async def test_transport_writes_404_wrapped_and_preserves_status(isolated_cache):
    # First request: 404 from the inner, cached WRAPPED. Second request: served
    # from cache WITH the 404 status — not as a fake 200.
    inner = _FakeInner(httpx.Response(404, json={"message": "no route"}))
    transport = CachingTransport(inner=inner)
    async with httpx.AsyncClient(transport=transport) as client:
        first = await client.get(URL, params=AUTH_PARAMS)
        second = await client.get(URL, params=AUTH_PARAMS)
    assert first.status_code == 404 and second.status_code == 404
    assert len(inner.requests) == 1  # the second was served from cache
    entry = get_cached("GET", URL, STRIPPED_PARAMS, None)
    assert entry is not None
    assert entry["_cached_status"] == 404
    assert entry["_cached_body"] == {"message": "no route"}


@pytest.mark.asyncio
async def test_transport_evicts_raw_transient_body(isolated_cache):
    # Raw legacy 500 body (httpStatusCode) is poison — evicted, inner served.
    set_cached("GET", URL, STRIPPED_PARAMS, None, {"$type": "ApiError", "httpStatusCode": 500})
    inner = _FakeInner(httpx.Response(200, json={"journeys": [{"duration": 5}]}))
    async with httpx.AsyncClient(transport=CachingTransport(inner=inner)) as client:
        resp = await client.get(URL, params=AUTH_PARAMS)
    assert resp.status_code == 200
    assert inner.requests  # poison did not short-circuit
    entry = get_cached("GET", URL, STRIPPED_PARAMS, None)
    assert entry is None or "httpStatusCode" not in entry


def test_scrub_removes_raw_key_value_even_escaped(isolated_cache, monkeypatch):
    """The query regex misses escaped/JSON-encoded echoes — the raw key
    VALUE must be scrubbed too (the security review's incomplete-scrub
    finding)."""
    from houses import api_cache

    monkeypatch.setattr(api_cache, "_cached_secret_key_value", "super-secret-key")

    blob = {
        "message": "the url was /path?app_key=super-secret-key",
        "json": '{"url": "https://api.tfl.gov.uk/?app_key=super-secret-key"}',
        "plain": "no key here",
    }
    out = api_cache._scrub_secrets(blob)
    assert "super-secret-key" not in json.dumps(out)
    assert "REDACTED" in out["message"]
    assert "REDACTED" in out["json"]
    assert out["plain"] == "no key here"


def test_set_cached_scrubs_app_key_from_body(isolated_cache):
    """Response bodies that echo the request URL carry the API key — it
    must be scrubbed before the body hits disk."""
    from houses.api_cache import get_cached, set_cached

    set_cached(
        "GET",
        "https://api.tfl.gov.uk/Journey/JourneyResults/51.5,-0.1/to/SW1V 2QQ",
        {"nationalSearch": "true"},
        None,
        {
            "relativeUri": "/Journey/JourneyResults/51.5,-0.1/to/SW1V%202QQ?nationalSearch=true&app_key=super-secret-key",  # noqa: E501
            "nested": {"uri": "?x=1&app_key=another-secret"},
            "plain": "no key here",
        },
    )
    cached = get_cached(
        "GET", "https://api.tfl.gov.uk/Journey/JourneyResults/51.5,-0.1/to/SW1V 2QQ", {"nationalSearch": "true"}
    )  # noqa: E501
    assert cached is not None
    assert "super-secret-key" not in json.dumps(cached)
    assert "another-secret" not in json.dumps(cached)
    assert "app_key=REDACTED" in cached["relativeUri"]
    assert "app_key=REDACTED" in cached["nested"]["uri"]
    assert cached["plain"] == "no key here"


@pytest.mark.asyncio
async def test_is_transient_error_body_classification():
    assert TflClient._is_transient_error_body({"_cached_status": 429}) is True
    assert TflClient._is_transient_error_body({"_cached_status": 503}) is True
    assert TflClient._is_transient_error_body({"httpStatusCode": 500}) is True
    assert TflClient._is_transient_error_body({"_cached_status": 401}) is True  # key expiry
    assert TflClient._is_transient_error_body({"_cached_status": 403}) is True
    assert TflClient._is_transient_error_body({"_cached_status": 409}) is True  # planner outage
    assert TflClient._is_transient_error_body({"_cached_status": 404}) is False  # deterministic no-route
    assert TflClient._is_transient_error_body({"httpStatusCode": 404}) is False
    assert TflClient._is_transient_error_body({"_cached_status": 300}) is False
    assert TflClient._is_transient_error_body({"journeys": []}) is False


@pytest.mark.asyncio
async def test_cached_api_call_does_not_cache_409_or_401(isolated_cache):
    # Auth failures (401/403) and planner outages (409) are transient — they
    # must not poison the cache like a genuine 404 no-route does.
    url_409 = f"{URL}/a"
    url_401 = f"{URL}/b"

    def client_409(**kwargs):
        return httpx.AsyncClient(
            transport=CachingTransport(inner=_FakeInner(httpx.Response(409, json={"message": "planner down"})))
        )

    with pytest.raises(HttpError):
        await TflClient._cached_api_call(url_409, AUTH_PARAMS, _client_factory=client_409)
    assert get_cached("GET", url_409, STRIPPED_PARAMS, None) is None  # 409 NOT cached

    def client_401(**kwargs):
        return httpx.AsyncClient(
            transport=CachingTransport(inner=_FakeInner(httpx.Response(401, json={"message": "bad key"})))
        )

    with pytest.raises(HttpError):
        await TflClient._cached_api_call(url_401, AUTH_PARAMS, _client_factory=client_401)
    assert get_cached("GET", url_401, STRIPPED_PARAMS, None) is None  # 401 NOT cached


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
