"""Unit tests for walkability enrichment — town extraction and fallback."""

from __future__ import annotations

import pytest

from houses.walkability import extract_town


class TestExtractTown:
    """extract_town parses the town name from a property address."""

    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("31 Example Road, Anytown, AB1 2CD", "Anytown"),
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
            # The town shares a segment with its postcode — strip the postcode,
            # don't drop the town with it (90970053's address shape).
            ("2 Huntingdon Gardens, Newbury RG14 2RG", "Newbury"),
            ("", ""),
        ],
    )
    def testextract_town(self, address: str, expected: str) -> None:
        assert extract_town(address) == expected

    def test_removes_counties(self) -> None:
        assert extract_town("Hawkins Way, Fleet, Hampshire, GU52 7JX") == "Fleet"
        assert extract_town("Park Road, Didcot, Oxfordshire, OX11 8QP") == "Didcot"

    def test_strips_trailing_descriptions(self) -> None:
        assert extract_town("Bourne End - Backing the River Wye, SL8 5HR") == "Bourne End"

    def test_handles_postcode_embedded_in_segment(self) -> None:
        """e.g. 'Surrey. KT9 2HN' where postcode is not at segment start."""
        assert extract_town("Leatherhead Road, Chessington, Surrey. KT9 2HN") == "Chessington"

    def test_returns_empty_for_address_with_only_postcode(self) -> None:
        assert extract_town("SW1P 1AA") == ""

    def test_returns_empty_for_empty_address(self) -> None:
        assert extract_town("") == ""
        assert extract_town("  ") == ""

    @pytest.mark.asyncio
    async def test_reverse_geocode_fallback(self) -> None:
        """When address-based town fails, reverse geocode should provide a fallback."""
        from houses.geopoint import GeoPoint
        from houses.walkability import WalkabilityFns, enrich_walkability

        async def mock_centre_fails(_lat, _lng, _town):
            return None  # address-based town resolution fails

        async def mock_rev(_lat, _lng):
            return GeoPoint(51.5, -0.1)

        async def mock_dur(_lat, _lng, _centre):
            return 15

        async def mock_amen(_lat, _lng):
            return ""

        result = await enrich_walkability(
            51.5,
            -0.1,
            "Some Street, SW1P 1AA",
            fns=WalkabilityFns(
                extract_town_centre=mock_centre_fails,
                walk_duration=mock_dur,
                reverse_geocode=mock_rev,
                nearby_amenities=mock_amen,
            ),
        )
        # Address branch produced no time, so the fallback supplied 15
        assert result.walk_to_town is not None
        assert result.walk_to_town.magnitude == 15, (
            f"Expected 15 from reverse geocode fallback, got {result.walk_to_town}"
        )

    @pytest.mark.asyncio
    async def test_reverse_geocode_not_needed_when_address_works(self) -> None:
        """When address-based town gives a valid walk time, don't call reverse geocode."""
        from houses.geopoint import GeoPoint
        from houses.walkability import WalkabilityFns, enrich_walkability

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

        result = await enrich_walkability(
            51.5,
            -0.1,
            "Some Street, Anytown, AB1 2CD",
            fns=WalkabilityFns(
                extract_town_centre=mock_extract,
                walk_duration=mock_dur,
                reverse_geocode=mock_rev,
                nearby_amenities=mock_amen,
            ),
        )
        assert result.walk_to_town is not None
        assert result.walk_to_town.magnitude == 10
        assert not rev_called, "Reverse geocode should NOT be called when address works"


def test_format_places_names_destinations_only() -> None:
    """The amenities string names places you walk TO, not hubs or areas."""
    from houses.apis.places_result import PlacesResult
    from houses.geopoint import GeoPoint
    from houses.walkability import _format_places

    here = GeoPoint(lat=51.6102143, lon=-1.2528258)
    places = [
        PlacesResult(name="Lidl", location=here, types=["supermarket"]),
        PlacesResult(name="Didcot Parkway", location=GeoPoint(lat=51.61, lon=-1.24), types=["transit_station"]),
        PlacesResult(name="Didcot", location=GeoPoint(lat=51.6, lon=-1.25), types=["locality"]),
    ]

    text = _format_places(places, here.lat, here.lon)

    assert "Lidl" in text
    assert "Didcot Parkway" not in text
    assert "Didcot" not in text


def test_format_overpass_names_named_elements_only() -> None:
    """The fallback summary drops nameless elements and cannot time a point it lacks."""
    from houses.apis.overpass_element import OverpassElement
    from houses.apis.overpass_response import OverpassResponse
    from houses.geopoint import GeoPoint
    from houses.walkability import _format_overpass

    here = GeoPoint(lat=51.6102143, lon=-1.2528258)
    data = OverpassResponse(
        elements=[
            OverpassElement(tags={"name": "Lidl"}, latitude=here.lat, longitude=here.lon, center=None),
            OverpassElement(tags={}, latitude=here.lat, longitude=here.lon, center=None),
            OverpassElement(tags={"name": "Far Park"}, latitude=None, longitude=None, center=None),
        ]
    )

    text = _format_overpass(data, here.lat, here.lon)

    assert "Lidl (1m)" in text  # at the origin: the floor is a minute, never 0
    assert "Far Park" in text  # named but untimed, so it sorts last
    assert text.count("|") == 1  # the nameless element is dropped
