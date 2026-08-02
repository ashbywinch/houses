"""Name-origin fallback — TfL 404s on coordinate origins for many stations."""

from __future__ import annotations

import pytest

from tools.commute.station_shed import Station, origin_candidates, route_station_duration

PBO = Station("Peterborough", "PBO", 52.5648, -0.2370)


def test_origin_candidates_coords_first():
    # Note: str(-0.2370) == "-0.237" — float repr drops trailing zeros.
    assert origin_candidates(PBO) == ["52.5648,-0.237", "Peterborough Rail Station"]


def test_origin_candidates_appends_suffix():
    st = Station("Bristol Temple Meads", "BRI", 51.4491, -2.5813)
    assert origin_candidates(st) == ["51.4491,-2.5813", "Bristol Temple Meads Rail Station"]


def test_origin_candidates_keeps_existing_suffix():
    st = Station("Reading Rail Station", "RDG", 51.4599, -0.9705)
    assert origin_candidates(st) == ["51.4599,-0.9705", "Reading Rail Station"]


class _FakeFetch:
    """Records URLs tried; returns canned data per URL."""

    def __init__(self, results: dict[str, dict | None]):
        self.results = results
        self.called: list[str] = []

    async def __call__(self, url: str, params: dict) -> dict | None:
        self.called.append(url)
        return self.results.get(url)


_COORDS_URL = "https://api.tfl.gov.uk/Journey/JourneyResults/52.5648,-0.237/to/SW1V 2QQ"
_NAME_URL = "https://api.tfl.gov.uk/Journey/JourneyResults/Peterborough Rail Station/to/SW1V 2QQ"


@pytest.mark.asyncio
async def test_coords_success_never_tries_name():
    fetch = _FakeFetch({_COORDS_URL: {"journeys": [{"duration": 77}]}})
    dur = await route_station_duration(PBO, "SW1V 2QQ", fetch=fetch)
    assert dur == 77
    assert fetch.called == [_COORDS_URL]


@pytest.mark.asyncio
async def test_cached_error_body_falls_through_to_name():
    # A cached error body is a dict with no journeys (TfL 404/error responses are
    # cached by _cached_api_call) — not None. It must NOT short-circuit the loop.
    fetch = _FakeFetch({_COORDS_URL: {"$type": "Error"}, _NAME_URL: {"journeys": [{"duration": 79}]}})
    dur = await route_station_duration(PBO, "SW1V 2QQ", fetch=fetch)
    assert dur == 79
    assert fetch.called == [_COORDS_URL, _NAME_URL]


@pytest.mark.asyncio
async def test_coords_failure_falls_back_to_name():
    fetch = _FakeFetch({_COORDS_URL: None, _NAME_URL: {"journeys": [{"duration": 100}]}})
    dur = await route_station_duration(PBO, "SW1V 2QQ", fetch=fetch)
    assert dur == 100
    assert fetch.called == [_COORDS_URL, _NAME_URL]


@pytest.mark.asyncio
async def test_both_fail_returns_none():
    fetch = _FakeFetch({_COORDS_URL: None, _NAME_URL: None})
    assert await route_station_duration(PBO, "SW1V 2QQ", fetch=fetch) is None
