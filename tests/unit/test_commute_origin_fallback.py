"""Name-origin fallback — TfL 404s on coordinate origins for many stations."""

from __future__ import annotations

import pytest

from tools.commute.station_shed import Station, origin_candidates, route_station_duration

PBO = Station("Peterborough", "PBO", 52.5648, -0.2370)
DEST = "SW1V 2QQ"
COORDS = "52.5648,-0.237"
NAME = "Peterborough Rail Station"


def test_origin_candidates_coords_first():
    # Note: str(-0.2370) == "-0.237" — float repr drops trailing zeros.
    assert origin_candidates(PBO) == [COORDS, NAME]


def test_origin_candidates_appends_suffix():
    st = Station("Bristol Temple Meads", "BRI", 51.4491, -2.5813)
    assert origin_candidates(st) == ["51.4491,-2.5813", "Bristol Temple Meads Rail Station"]


def test_origin_candidates_keeps_existing_suffix():
    st = Station("Reading Rail Station", "RDG", 51.4599, -0.9705)
    assert origin_candidates(st) == ["51.4599,-0.9705", "Reading Rail Station"]


class _FakeFetch:
    """Records origins tried; returns canned durations per (origin, dest)."""

    def __init__(self, results: dict[tuple[str, str], int | None]):
        self.results = results
        self.called: list[str] = []

    async def __call__(self, origin: str, dest: str) -> int | None:
        self.called.append(origin)
        return self.results.get((origin, dest))


@pytest.mark.asyncio
async def test_coords_success_never_tries_name():
    fetch = _FakeFetch({(COORDS, DEST): 77})
    assert await route_station_duration(PBO, DEST, fetch=fetch) == 77
    assert fetch.called == [COORDS]


@pytest.mark.asyncio
async def test_coords_failure_falls_back_to_name():
    fetch = _FakeFetch({(COORDS, DEST): None, (NAME, DEST): 100})
    assert await route_station_duration(PBO, DEST, fetch=fetch) == 100
    assert fetch.called == [COORDS, NAME]


@pytest.mark.asyncio
async def test_both_fail_returns_none():
    fetch = _FakeFetch({(COORDS, DEST): None, (NAME, DEST): None})
    assert await route_station_duration(PBO, DEST, fetch=fetch) is None


# ── TflClient.route_duration — the public API the tool routes through ──


class _FakeCachedFetch:
    """Injected fetch; records (url, params) and returns canned data."""

    def __init__(self, result: dict | None):
        self.result = result
        self.called: list[tuple[str, dict]] = []

    async def __call__(self, url: str, params: dict) -> dict | None:
        self.called.append((url, params))
        return self.result


@pytest.mark.asyncio
async def test_route_duration_returns_none_on_journey_less_body():
    from houses.tfl_client import TflClient

    fetch = _FakeCachedFetch({"$type": "Tfl.Api.Presentation.Entities.ApiError, Tfl.Api.Presentation.Entities"})
    assert await TflClient.route_duration(COORDS, DEST, fetch=fetch) is None


@pytest.mark.asyncio
async def test_route_duration_parses_best_journey():
    from houses.tfl_client import TflClient

    fetch = _FakeCachedFetch({"journeys": [{"duration": 79}, {"duration": 95}]})
    assert await TflClient.route_duration(COORDS, DEST, fetch=fetch) == 79


@pytest.mark.asyncio
async def test_route_duration_builds_national_search_request():
    from houses.tfl_client import TflClient

    fetch = _FakeCachedFetch({"journeys": [{"duration": 79}]})
    await TflClient.route_duration(COORDS, DEST, fetch=fetch)
    url, params = fetch.called[0]
    assert url == "https://api.tfl.gov.uk/Journey/JourneyResults/52.5648,-0.237/to/SW1V 2QQ"
    assert params["nationalSearch"] == "true"
    assert params["timeIs"] == "arriving"
    assert "bus" in params["mode"]
    assert "tube" in params["mode"]


@pytest.mark.asyncio
async def test_route_duration_respects_allow_bus():
    from houses.tfl_client import TflClient

    fetch = _FakeCachedFetch({"journeys": [{"duration": 79}]})
    await TflClient.route_duration(COORDS, DEST, allow_bus=False, fetch=fetch)
    assert "bus" not in fetch.called[0][1]["mode"]
