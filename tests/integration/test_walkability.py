"""Integration test for walkability enrichment — walk times on amenities.

Uses the committed fixture cache (tests/fixtures/api_cache/) so this test
only needs API access once — thereafter it reads cached responses.
"""

from pathlib import Path

import pytest

from houses.api_cache import set_cache_dir


@pytest.fixture(autouse=True)
def _use_fixture_cache():
    """Use the committed fixture cache so this test works in CI."""
    cache_dir = Path(__file__).parent.parent / "fixtures" / "api_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    set_cache_dir(cache_dir)


@pytest.mark.asyncio
async def test_amenities_include_walk_times():
    from houses.walkability import enrich_walkability

    result = await enrich_walkability(
        lat=51.521,
        lng=-0.720,
        address="Maidenhead Station Area, Maidenhead, SL6",
    )
    amenities = result.get("amenities", "")
    assert amenities, f"Amenities should not be empty, got: {amenities!r}"

    parts = amenities.split(" | ")
    for part in parts:
        assert "(" in part and ")" in part and "m" in part, (
            f"Each amenity should have walk time like 'Name (Xm)', got: {part!r}"
        )
        name, time_part = part.rsplit("(", 1)
        time_str = time_part.rstrip(")")
        assert time_str.endswith("m"), f"Walk time should end with 'm', got: {time_str!r}"
        minutes = int(time_str.rstrip("m"))
        assert 1 <= minutes <= 60, f"Walk time should be 1-60 min, got: {minutes}"

    assert result.get("walk_to_town_minutes") is not None, "Walk to town time not found"
    assert 1 <= result["walk_to_town_minutes"] <= 60
