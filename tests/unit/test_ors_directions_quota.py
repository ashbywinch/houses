"""ORS daily-quota guard — the directions/walk/drive path uses the shared
geocoder state (houses.location._GeoState) that already existed.

2026-09-24: ORS answers its DAILY quota with 403 (per-minute with 429).
The geocoding path already paced and short-circuited on the flag; the
directions/walk/drive path had neither, so a cache-cold double recompute
(live + smoke on a fresh box) burned the quota in minutes and every later
property was a wasted 403 round-trip. These tests pin: a 403 sets the
shared flag, subsequent calls short-circuit without a network hit, and
the callsite paces through the shared timestamp.
"""

import httpx
import pytest

from houses import location, walkability
from houses.geopoint import GeoPoint

pytestmark = pytest.mark.asyncio

_LAT = 51.3458
_LNG = -0.5011
_DEST = GeoPoint(lat=51.5074, lon=-0.1276)


@pytest.fixture(autouse=True)
def _clean_state():
    state = location.get_geo_state()
    state.ors_exhausted = False
    state.ors_directions_last_call = 0.0
    yield
    state = location.get_geo_state()
    state.ors_exhausted = False
    state.ors_directions_last_call = 0.0


class _FourZeroThreeClient:
    """httpx-shaped fake: every ORS POST answers 403 (the daily quota)."""

    def __init__(self) -> None:
        self.posts: list[str] = []

    async def __aenter__(self) -> "_FourZeroThreeClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, url: str, *args: object, **kwargs: object) -> httpx.Response:
        self.posts.append(url)
        request = httpx.Request("POST", url)
        raise httpx.HTTPStatusError(
            f"Client error '403 Forbidden' for url '{url}'",
            request=request,
            response=httpx.Response(403, request=request),
        )


async def test_walk_403_sets_shared_flag_and_short_circuits():
    client = _FourZeroThreeClient()
    result = await walkability._walk_duration(
        _LAT, _LNG, _DEST, _client_factory=lambda **k: client
    )
    assert result is None
    assert location.get_geo_state().ors_exhausted, "a 403 must flip the shared flag"
    assert len(client.posts) == 1

    # second call: the shared flag short-circuits — NO network hit
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
    assert location.get_geo_state().ors_directions_last_call > 0.0