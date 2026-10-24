"""The Google Places row: reading it, and deciding what counts as a place.

Places answers a nearby search with areas and transit hubs alongside
destinations — a "locality" is a town, an "administrative_area_level_3" a
borough, a "bus_stop" a point you pass through. Walkability lists destinations,
so that judgement belongs with the row it judges, not in the formatter.
"""

from __future__ import annotations

from houses.apis.places_result import PlacesResult
from houses.geopoint import GeoPoint

# The property's point and its town centre, as the fixtures use them.
HERE = GeoPoint(lat=51.6102143, lon=-1.2528258)
TOWN_CENTRE = GeoPoint(lat=51.6, lon=-1.25)


def test_a_supermarket_is_a_destination():
    result = PlacesResult(name="Lidl", location=HERE, types=["supermarket", "store"])

    assert result.is_destination()


def test_a_transit_hub_is_not_a_destination():
    for types in (["transit_station"], ["bus_stop"], ["bus_station", "point_of_interest"]):
        result = PlacesResult(name="Didcot Parkway", location=HERE, types=types)
        assert not result.is_destination(), types


def test_an_area_is_not_a_destination():
    """A town, a borough and a district are not somewhere you walk TO."""
    for types in (["locality"], ["administrative_area_level_3"], ["administrative_area_level_4"]):
        result = PlacesResult(name="Didcot", location=TOWN_CENTRE, types=types)
        assert not result.is_destination(), types


def test_from_place_reads_a_google_place():
    result = PlacesResult.from_place(
        {
            "displayName": {"text": "Lidl"},
            "types": ["supermarket", "store"],
            "location": {"latitude": HERE.lat, "longitude": HERE.lon},
        }
    )

    assert result.name == "Lidl"
    assert result.location == HERE
    assert result.types == ["supermarket", "store"]


def test_from_place_without_coordinates_has_no_location():
    """Places may omit the point; the row must not invent (0, 0)."""
    result = PlacesResult.from_place({"displayName": {"text": "Nameless"}, "types": ["store"]})

    assert result.name == "Nameless"
    assert result.location is None


def test_from_place_without_a_display_name_is_blank():
    assert PlacesResult.from_place({"location": {"latitude": TOWN_CENTRE.lat, "longitude": TOWN_CENTRE.lon}}).name == ""
