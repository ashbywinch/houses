"""Integration test for walkability enrichment — walk times on amenities."""

import pytest

from houses.walkability import enrich_walkability

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_amenities_include_walk_times():
    """Amenities should include walk times in the format 'Name (Xm)'."""
    # Maidenhead — known town with amenities nearby
    result = await enrich_walkability(
        lat=51.521,   # Maidenhead station area
        lng=-0.720,
        address="Maidenhead Station Area, Maidenhead, SL6",
    )
    amenities = result.get("amenities", "")
    assert amenities, f"Amenities should not be empty, got: {amenities!r}"

    # Each entry should have a walk time like "(5m)" or "(12m)"
    parts = amenities.split(" | ")
    for part in parts:
        assert "(" in part and ")" in part and "m" in part, \
            f"Each amenity should have walk time like 'Name (Xm)', got: {part!r}"
        name, time_part = part.rsplit("(", 1)
        time_str = time_part.rstrip(")")
        assert time_str.endswith("m"), f"Walk time should end with 'm', got: {time_str!r}"
        minutes = int(time_str.rstrip("m"))
        assert 1 <= minutes <= 60, f"Walk time should be 1-60 min, got: {minutes}"

    # walk_to_town_minutes should also be present and reasonable
    assert result.get("walk_to_town_minutes") is not None
    assert 1 <= result["walk_to_town_minutes"] <= 60
