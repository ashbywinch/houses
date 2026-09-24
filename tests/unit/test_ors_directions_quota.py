"""The external-API gateway quota guard — ORS's DAILY quota (403) sets the
process-wide flag; later calls short-circuit without a network hit.

2026-09-24: a cache-cold double recompute burned ORS's daily quota in
minutes; every later property was a wasted 403 round-trip and the
park-and-ride leg raised permanent impossibles. The gateway gives the
directions/walk/drive calls the same guard the geocoders already had —
without per-callsite wiring.
"""

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

    async def request(
        self, method: str, url: str, *args: object, **kwargs: object
    ) -> httpx.Response:
        self.posts.append(url)
        request = httpx.Request(method, url)
        raise httpx.HTTPStatusError(
            f"Client error '403 Forbidden' for url '{url}'",
            request=request,
            response=httpx.Response(403, request=request),
        )


async def test_walk_403_sets_the_shared_flag_and_short_circuits():
    client = _FourZeroThreeClient()
    result = await walkability._walk_duration(
        _LAT, _LNG, _DEST, _client_factory=lambda **k: client
    )
    assert result is None
    assert apigw.GATE.quota_exhausted(apigw.ORS), "a 403 must flip the shared flag"
    assert len(client.posts) == 1

    # second call: the flag short-circuits — NO network hit
    result2 = await walkability._walk_duration(
        _LAT, _LNG, _DEST, _client_factory=lambda **k: client
    )
    assert result2 is None
    assert len(client.posts) == 1, "exhausted calls must not reach ORS"


async def test_failed_call_updates_the_pacing_timestamp():
    client = _FourZeroThreeClient()
    result = await walkability._walk_duration(
        _LAT, _LNG, _DEST, _client_factory=lambda **k: client
    )
    assert result is None
    assert apigw.GATE._last_call[apigw.ORS] > 0.0


def test_quota_flag_resets():
    apigw.GATE.mark_quota_exhausted(apigw.ORS)
    assert apigw.GATE.quota_exhausted(apigw.ORS)
    apigw.GATE.clear_quota(apigw.ORS)
    assert not apigw.GATE.quota_exhausted(apigw.ORS)