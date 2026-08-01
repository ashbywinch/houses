"""Unit tests for walkability enrichment — town extraction and fallback."""

from __future__ import annotations

import pytest

from houses.walkability import _extract_town


class TestExtractTown:
    """_extract_town parses the town name from a property address."""

    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("31 Isambard Road, Southall, UB2 4GN", "Southall"),
            ("Boyn Valley Rd, Maidenhead, SL6 4DT", "Maidenhead"),
            ("Thurlby Way, Maidenhead, SL6 3YZ", "Maidenhead"),
            ("Blue Dawes, Pangbourne on Thames, RG8 7AS", "Pangbourne on Thames"),
            ("Grand Drive, London, SW20 9NB", "London"),
            ("Bourne End - Backing the River Wye, SL8 5HR", "Bourne End"),
            ("Hawkins Way, Fleet, Hampshire, GU52 7JX", "Fleet"),
            ("Leatherhead Road, Chessington, Surrey. KT9 2HN", "Chessington"),
            ("Woking, GU22 9PX", "Woking"),
            ("High Wycombe, HP13", "High Wycombe"),
            ("14 Burnside, Fleet, GU51 3RE", "Fleet"),
            ("Molesey Road, Hersham, Walton-On-Thames, KT12 4QW", "Walton-On-Thames"),
            ("Norden Road, Maidenhead, SL6 4AY", "Maidenhead"),
            ("", ""),
        ],
    )
    def test_extract_town(self, address: str, expected: str) -> None:
        assert _extract_town(address) == expected

    def test_removes_counties(self) -> None:
        assert _extract_town("Hawkins Way, Fleet, Hampshire, GU52 7JX") == "Fleet"
        assert _extract_town("Park Road, Didcot, Oxfordshire, OX11 8QP") == "Didcot"

    def test_strips_trailing_descriptions(self) -> None:
        assert _extract_town("Bourne End - Backing the River Wye, SL8 5HR") == "Bourne End"

    def test_handles_postcode_embedded_in_segment(self) -> None:
        """e.g. 'Surrey. KT9 2HN' where postcode is not at segment start."""
        assert _extract_town("Leatherhead Road, Chessington, Surrey. KT9 2HN") == "Chessington"

    def test_returns_empty_for_address_with_only_postcode(self) -> None:
        assert _extract_town("SW1V 2QQ") == ""

    def test_returns_empty_for_empty_address(self) -> None:
        assert _extract_town("") == ""
        assert _extract_town("  ") == ""

    @pytest.mark.asyncio
    async def test_reverse_geocode_fallback(self) -> None:
        """When address-based town fails, reverse geocode should provide a fallback."""
        # Mock the reverse geocode to avoid real API calls
        import houses.walkability as w
        from houses.walkability import enrich_walkability

        original_rev = w._find_town_centre_by_reverse_geocode
        original_dur = w._walk_duration
        original_amen = w._nearby_amenities

        async def mock_rev(_lat, _lng):
            from houses.geo import GeoPoint

            return GeoPoint(51.5, -0.1)

        async def mock_dur(_lat, _lng, _centre):
            return 15

        async def mock_amen(_lat, _lng):
            return ""

        w._find_town_centre_by_reverse_geocode = mock_rev
        w._walk_duration = mock_dur
        w._nearby_amenities = mock_amen
        try:
            # Address has no town to extract — should fall back to reverse geocode
            result = await enrich_walkability(51.5, -0.1, "Some Street, SW1V 2QQ")
            assert result["walk_to_town"]["value"] == 15, (
                f"Expected 15 from reverse geocode fallback, got {result['walk_to_town']}"
            )
        finally:
            w._find_town_centre_by_reverse_geocode = original_rev
            w._walk_duration = original_dur
            w._nearby_amenities = original_amen

    @pytest.mark.asyncio
    async def test_reverse_geocode_not_needed_when_address_works(self) -> None:
        """When address-based town gives a valid walk time, don't call reverse geocode."""
        import houses.walkability as w
        from houses.geo import GeoPoint
        from houses.walkability import enrich_walkability

        original_rev = w._find_town_centre_by_reverse_geocode
        original_extract = w._extract_town_centre
        original_dur = w._walk_duration
        original_amen = w._nearby_amenities

        rev_called = False

        async def mock_extract(_lat, _lng, _town):
            return GeoPoint(51.5, -0.1)

        async def mock_dur(_lat, _lng, _centre):
            return 10

        async def mock_rev(_lat, _lng):
            nonlocal rev_called
            rev_called = True
            return GeoPoint(51.5, -0.1)

        async def mock_amen(_lat, _lng):
            return ""

        w._extract_town_centre = mock_extract
        w._walk_duration = mock_dur
        w._find_town_centre_by_reverse_geocode = mock_rev
        w._nearby_amenities = mock_amen
        try:
            result = await enrich_walkability(51.5, -0.1, "Some Street, Southall, UB2 4GN")
            assert result["walk_to_town"]["value"] == 10
            assert not rev_called, "Reverse geocode should NOT be called when address works"
        finally:
            w._extract_town_centre = original_extract
            w._walk_duration = original_dur
            w._find_town_centre_by_reverse_geocode = original_rev
            w._nearby_amenities = original_amen
