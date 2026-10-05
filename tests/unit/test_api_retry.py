"""The API→DAG retry contract: transient errors must reach the DAG retry
scheduler with the provider's interval, and the daily-quota 403 family must
classify as permanent "daily_quota" — never a wasted retry.

Three gaps closed 2026-09-24:
1. ``DailyQuotaError`` carries ``is_daily_quota``; the classifier matches the
   marker (the old name-string match silently misfired).
2. The gateway attaches the provider's ``Retry-After`` to transient httpx
   errors, and ``_retry_delay_from`` honors windows beyond the 5-minute
   backoff cap.
3. The last-resort geocoders (Nominatim, postcodes.io) surface 429/5xx and
   network errors as exceptions so the DAG retries — previously they were
   permanent impossibles.

Every fake here RAISES before the cache layer sees a response, so no unit
run touches the disk cache (the conftest invariant).
"""

from __future__ import annotations

from datetime import timedelta
from typing import override

import httpx
import pytest

from dag.attempt import Attempt, classify_exception
from dag.derived_node import DerivedNode
from dag.scheduler import AsyncQueueScheduler, set_scheduler
from houses import apigw, apis
from houses.geopoint import GeoPoint
from houses.location import _geocode_nominatim, _geocode_ors, _geocode_postcode

pytestmark = pytest.mark.asyncio

_LAT = 51.3458
_LNG = -0.5011
_DEST = GeoPoint(lat=51.5074, lon=-0.1276)
_URL = "https://api.heigit.org/openrouteservice/v2/directions/foot-walking/whatever"


def _status_response(status: int, url: str = _URL, headers: dict[str, str] | None = None) -> httpx.Response:
    request = httpx.Request("POST", url)
    return httpx.Response(status, request=request, headers=headers or {})


@pytest.fixture(autouse=True)
def _clean_gate():
    apigw.GATE.clear_quota()
    apigw.GATE.reset_pacing()
    yield
    apigw.GATE.clear_quota()
    apigw.GATE.reset_pacing()


class _RaisingClient:
    """httpx-shaped fake: request() raises a fixed status (client-raised
    path) or returns a fixed response (raise_for_status path)."""

    def __init__(self, status: int, headers: dict[str, str] | None = None, *, raise_in_request: bool = True):
        self._status = status
        self._headers = headers or {}
        self._raise_in_request = raise_in_request

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def request(self, method: str, url: str, *args: object, **kwargs: object) -> httpx.Response:
        if self._raise_in_request:
            request = httpx.Request(method, url)
            raise httpx.HTTPStatusError(
                f"HTTP {self._status} for {url}",
                request=request,
                response=httpx.Response(self._status, request=request, headers=self._headers),
            )
        return _status_response(self._status, url, self._headers)


class _NetworkErrorClient:
    """httpx-shaped fake: request() raises a network error."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def request(self, method: str, url: str, *args: object, **kwargs: object) -> httpx.Response:
        raise httpx.RequestError(f"connection refused for {url}", request=httpx.Request(method, url))


class _DelayProbeNode(DerivedNode[str]):
    """A live DerivedNode whose retry counter we drive for delay math."""

    def __init__(self) -> None:
        super().__init__("delay_probe", str, deps=())
        self._retry_count = 0

    @override
    def compute(self, *dep_attempts: Attempt) -> Attempt[str]:
        return Attempt.succeeded("ok")


# ── 1. the daily-quota family classifies as permanent ──────────────


def test_daily_quota_error_classifies_permanent():
    classification = classify_exception(apigw.DailyQuotaError("ors: quota"))
    assert classification.code == "daily_quota"
    assert classification.retryable is False


# ── 2. provider Retry-After reaches the retry scheduler ─────────────


async def test_transient_429_carries_provider_retry_after():
    """A 429 with Retry-After: 120 must surface with retry_after=120 so
    the DAG's scheduler waits the provider's window, not the 10s backoff."""
    client = _RaisingClient(429, {"Retry-After": "120"})
    with pytest.raises(apigw.GatewayHttpError) as excinfo:
        await apis.ors.directions(
            GeoPoint(_LAT, _LNG),
            _DEST,
            mode="foot-walking",
            _client_factory=lambda **k: client,
        )
    assert isinstance(excinfo.value, httpx.HTTPStatusError), "callers catch the base class"
    assert excinfo.value.retry_after == 120.0
    assert classify_exception(excinfo.value).retryable, "429 must be retryable"


async def test_transient_503_from_raise_for_status_carries_retry_after():
    """A 503 returned (not raised) by the client still gets Retry-After
    attached at the raise_for_status boundary."""
    client = _RaisingClient(503, {"Retry-After": "45"}, raise_in_request=False)
    with pytest.raises(apigw.GatewayHttpError) as excinfo:
        await apis.ors.directions(
            GeoPoint(_LAT, _LNG),
            _DEST,
            mode="foot-walking",
            _client_factory=lambda **k: client,
        )
    assert excinfo.value.retry_after == 45.0
    assert classify_exception(excinfo.value).retryable


def test_retry_delay_honors_provider_window_beyond_backoff_cap():
    """An explicit Retry-After window is authoritative within a day; only
    the exponential backoff path keeps the 5-minute cap."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))
    node = _DelayProbeNode()
    node._retry_count = 1

    exc = apigw.GatewayHttpError(
        "too many",
        request=httpx.Request("GET", _URL),
        response=httpx.Response(429, request=httpx.Request("GET", _URL)),
        retry_after=3600,
    )
    assert node._retry_delay_from(exc) == timedelta(seconds=3600)

    exc2 = apigw.GatewayHttpError(
        "boom",
        request=httpx.Request("GET", _URL),
        response=httpx.Response(503, request=httpx.Request("GET", _URL)),
        retry_after=None,
    )
    assert node._retry_delay_from(exc2) == timedelta(seconds=20), "no Retry-After -> exponential backoff 10s * 2^1"


# ── 3. last-resort geocoders surface transients for DAG retry ───────


async def test_nominatim_transient_503_raises_for_dag_retry():
    """A 503 from the final geocoder must pend+retry, not permanently
    fail the property."""
    with pytest.raises(httpx.HTTPStatusError):
        await _geocode_nominatim("Maidenhead", _client_factory=lambda **k: _RaisingClient(503))


async def test_nominatim_permanent_404_stays_impossible():
    attempt = await _geocode_nominatim("Not A Place", _client_factory=lambda **k: _RaisingClient(404))
    assert attempt.impossible
    assert "404" in (attempt.error or "")


async def test_postcode_transient_503_raises_for_dag_retry():
    with pytest.raises(httpx.HTTPStatusError):
        await _geocode_postcode("SW1A 1AA", _client_factory=lambda **k: _RaisingClient(503))


async def test_ors_transient_503_raises_for_dag_retry():
    """ORS (the first fallback) must pend+retry on a transient 5xx — a
    swallow silently fell through to Nominatim and could permanently
    impossible a property on a temporary ORS outage (2026-09-28)."""
    with pytest.raises(httpx.HTTPStatusError):
        await _geocode_ors(
            "Maidenhead",
            "unit-test-ors-transient",
            _client_factory=lambda **k: _RaisingClient(503),
        )


async def test_postcode_network_error_raises_for_dag_retry():
    """A connection error must pend+retry — not 'unexpected error'
    impossible (the old broad-except swallowed it)."""
    with pytest.raises(httpx.RequestError):
        await _geocode_postcode("SW1A 1AA", _client_factory=lambda **k: _NetworkErrorClient())


async def test_postcode_404_stays_cached_impossible():
    attempt = await _geocode_postcode("XX9 9XX", _client_factory=lambda **k: _RaisingClient(404))
    assert attempt.impossible
    assert "404" in (attempt.error or "")


class _JsonClient:
    """httpx-shaped fake that RETURNS a fixed body + status, and counts
    requests — the two Google quota paths both go through the client."""

    def __init__(self, status: int, json_body: dict, *, hits: list):
        self._status = status
        self._json_body = json_body
        self.hits = hits

    async def request(self, method, url, *, headers, params=None, json=None):
        self.hits.append(url)
        return _JsonResponse(self._status, self._json_body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _JsonResponse:
    def __init__(self, status: int, json_body: dict):
        self.status_code = status
        self._json_body = json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://maps.googleapis.com/"),
                response=httpx.Response(self.status_code, request=httpx.Request("GET", "https://maps.googleapis.com/")),
            )
        return None

    def json(self):
        return self._json_body


async def _geocode_via(api, key: str, hits: list[str]):
    """One postcodes geocode through a stateless fake bound to ``hits``."""
    client = _JsonClient(200, {"result": {"latitude": 51.5, "longitude": -0.13}}, hits=hits)
    return await api.geocode(key, _client_factory=lambda *a, **k: client, _no_cache=True)


async def test_google_403_marks_quota_exhausted_and_skips_future_calls():
    """The 2026-09-24 regression: Google's profile had no quota status, so a
    403 repeated on every fallback instead of marking the key exhausted for
    the request run. A 403 must flip the gate ONCE and never request again."""
    hits: list[str] = []
    first = apis.google_geocode.geocode(
        "1 High Street",
        _client_factory=lambda *a, **k: _JsonClient(403, {}, hits=hits),
        _no_cache=True,
    )
    assert await first is None  # DailyQuotaError -> keep-fallback signal
    assert apigw.GATE.quota_exhausted(apigw.GOOGLE)
    # a subsequent call is skipped entirely — zero requests, not a repeat
    second = apis.google_geocode.geocode(
        "2 High Street",
        _client_factory=lambda *a, **k: _JsonClient(200, {"status": "OK", "results": []}, hits=hits),
        _no_cache=True,
    )
    assert await second is None
    assert len(hits) == 1, f"the exhausted key must not be asked again: {hits}"


async def test_google_over_query_limit_body_marks_exhausted():
    """Google reports the daily limit in the BODY (HTTP 200): the old
    OVER_QUERY_LIMIT check was lost in the migration — restore it, so one
    such response means no more Google calls for this run."""
    hits: list[str] = []

    def api(*a, **k):  # noqa: ANN002,ANN003 - the seam takes *a/**k, test fake
        return _JsonClient(200, {"status": "OVER_QUERY_LIMIT", "error_message": "quota"}, hits=hits)

    assert await apis.google_geocode.geocode("1 High Street", _client_factory=api, _no_cache=True) is None
    assert apigw.GATE.quota_exhausted(apigw.GOOGLE)
    assert await apis.google_geocode.geocode("2 High Street", _client_factory=api, _no_cache=True) is None
    assert len(hits) == 1, f"the exhausted key must not be asked again: {hits}"


async def test_postcodes_uses_the_outcode_url_only_for_an_outcode():
    """The discriminator is the postcodes.io contract — /outcodes/SW1A, not
    the full-postcode URL — and a real postcode (SW1A 1AA, digits AND a
    trailing letter) must use the postcodes search URL."""
    hits: list[str] = []
    for key, want in (
        ("SW1A", "https://api.postcodes.io/outcodes/SW1A"),
        ("SW1A 1AA", "https://api.postcodes.io/postcodes/SW1A 1AA"),
    ):
        hits.clear()
        assert await _geocode_via(apis.postcodes, key, hits) is not None
        assert hits == [want], (key, hits)
