"""The external-API gateway quota guard — ORS's DAILY quota (403) sets the
process-wide flag; later calls short-circuit without a network hit.

2026-09-24: a cache-cold double recompute burned ORS's daily quota in
minutes; every later property was a wasted 403 round-trip and the
park-and-ride leg raised permanent impossibles. The gateway gives the
directions/walk/drive calls the same guard the geocoders already had —
without per-callsite wiring.
"""

import logging

import httpx
import pytest

from houses import apigw, walkability
from houses.geopoint import GeoPoint

pytestmark = pytest.mark.asyncio

_LAT = 51.3458
_LNG = -0.5011
_DEST = GeoPoint(lat=51.5074, lon=-0.1276)


@pytest.fixture(autouse=True)
def _clean_gate():
    apigw.GATE.clear_quota()
    apigw.GATE.reset_pacing()
    yield
    apigw.GATE.clear_quota()
    apigw.GATE.reset_pacing()


class _FourZeroThreeClient:
    """httpx-shaped fake: every ORS POST answers 403 (the daily quota)."""

    def __init__(self) -> None:
        self.posts: list[str] = []

    async def __aenter__(self) -> "_FourZeroThreeClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def request(self, method: str, url: str, *args: object, **kwargs: object) -> httpx.Response:
        self.posts.append(url)
        request = httpx.Request(method, url)
        raise httpx.HTTPStatusError(
            f"Client error '403 Forbidden' for url '{url}'",
            request=request,
            response=httpx.Response(403, request=request),
        )


class _FourZeroFourClient:
    """httpx-shaped fake: every ORS POST answers 404 (no route exists)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def request(self, method: str, url: str, *args: object, **kwargs: object) -> httpx.Response:
        request = httpx.Request(method, url)
        raise httpx.HTTPStatusError(
            f"Client error '404 Not Found' for url '{url}'",
            request=request,
            response=httpx.Response(404, request=request),
        )


async def test_walk_404_no_route_keeps_the_walk_leg():
    """A 404 = ORS found no route between the points (e.g. no road
    connection): keep the walk leg — never an impossible pill, while the
    daily-quota flag stays untouched."""
    client = _FourZeroFourClient()
    result = await walkability._walk_duration(_LAT, _LNG, _DEST, _client_factory=lambda **k: client)
    assert result is None
    assert not apigw.GATE.quota_exhausted(apigw.ORS), "a 404 is not quota"


async def test_walk_403_sets_the_shared_flag_and_short_circuits():
    client = _FourZeroThreeClient()
    result = await walkability._walk_duration(_LAT, _LNG, _DEST, _client_factory=lambda **k: client)
    assert result is None
    assert apigw.GATE.quota_exhausted(apigw.ORS), "a 403 must flip the shared flag"
    assert len(client.posts) == 1

    # second call: the flag short-circuits — NO network hit
    result2 = await walkability._walk_duration(_LAT, _LNG, _DEST, _client_factory=lambda **k: client)
    assert result2 is None
    assert len(client.posts) == 1, "exhausted calls must not reach ORS"


async def test_failed_call_updates_the_pacing_timestamp():
    client = _FourZeroThreeClient()
    result = await walkability._walk_duration(_LAT, _LNG, _DEST, _client_factory=lambda **k: client)
    assert result is None
    assert apigw.GATE._last_call[apigw.ORS] > 0.0


def test_quota_flag_resets():
    apigw.GATE.mark_quota_exhausted(apigw.ORS)
    assert apigw.GATE.quota_exhausted(apigw.ORS)
    apigw.GATE.clear_quota(apigw.ORS)
    assert not apigw.GATE.quota_exhausted(apigw.ORS)


class _FourZeroThreeBodyClient:
    """httpx-shaped fake: 403 REFUSAL carrying the API's response body."""

    def __init__(self) -> None:
        self.posts: list[str] = []

    async def __aenter__(self) -> "_FourZeroThreeBodyClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def request(self, method: str, url: str, *args: object, **kwargs: object) -> httpx.Response:
        self.posts.append(url)
        request = httpx.Request(method, url)
        raise httpx.HTTPStatusError(
            f"Client error '403 Forbidden' for url '{url}'",
            request=request,
            response=httpx.Response(403, text='{"error": "Quota exceeded"}', request=request),
        )


async def test_refused_call_logs_the_caller_stack_and_the_body(caplog):
    """A refusal must be attributable: which app code asked, and what the
    API said. The body is the only thing telling "Quota exceeded" from
    "Invalid key" — that is the difference between a burned quota and a
    key that never had the endpoint."""
    client = _FourZeroThreeBodyClient()
    with caplog.at_level(logging.INFO):
        await walkability._walk_duration(_LAT, _LNG, _DEST, _client_factory=lambda **k: client)

    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "api call POST" in log, "the outbound call must be logged"
    assert "walkability.py:" in log, "the log must name the calling app code"
    assert "api response" in log and "HTTP 403" in log
    assert "Quota exceeded" in log, "the response body must be logged"


async def test_quota_error_carries_the_response_body():
    """The refusal body must survive into the raised error, not just the
    log — the surfaced message is what a human reads first."""
    client = _FourZeroThreeBodyClient()
    with pytest.raises(apigw.DailyQuotaError) as excinfo:
        await apigw.api_fetch(
            "POST",
            "https://api.openrouteservice.org/v2/directions/driving-car",
            api=apigw.ORS,
            _client_factory=lambda **k: client,
        )
    assert "Quota exceeded" in str(excinfo.value)


async def test_short_circuited_call_logs_the_caller_without_calling(caplog):
    """When the quota flag short-circuits, the wasted attempt is still
    logged with its caller — that count is how repeats get spotted."""
    client = _FourZeroThreeClient()
    apigw.GATE.mark_quota_exhausted(apigw.ORS)

    with caplog.at_level(logging.INFO):
        result = await walkability._walk_duration(_LAT, _LNG, _DEST, _client_factory=lambda **k: client)

    assert result is None
    assert client.posts == [], "a short-circuited call must not reach the API"
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "api skip" in log and "walkability.py:" in log
