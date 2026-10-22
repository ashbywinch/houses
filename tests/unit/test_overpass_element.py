"""The Overpass element's point, from whichever field carries it.

Overpass answers nodes with their own coordinates and ways with a ``center``;
the walkability query asks for both, so an element can carry either. A missing
point must read as missing — the amenity summary has to be able to say "not
timed" rather than place a shop at (0, 0).
"""

from __future__ import annotations

from houses.apis.overpass_element import OverpassElement
from houses.geopoint import GeoPoint

HERE = GeoPoint(lat=51.6102143, lon=-1.2528258)


def test_a_node_carries_its_own_coordinates():
    element = OverpassElement(tags={"name": "Lidl"}, latitude=HERE.lat, longitude=HERE.lon, center=None)

    assert element.location() == HERE


def test_a_way_carries_its_centre():
    element = OverpassElement(tags={"name": "Park"}, latitude=None, longitude=None, center=(HERE.lat, HERE.lon))

    assert element.location() == HERE


def test_an_element_with_no_point_has_no_location():
    element = OverpassElement(tags={"name": "Nameless"}, latitude=None, longitude=None, center=None)

    assert element.location() is None
