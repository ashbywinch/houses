"""A geocoder's "no idea" answer must never become a property's location.

2026-09-28: "Foxhall Road, Didcot, OX11" was stored at 54.75844,-2.69531 — a
Pelias fallback. Both parsers took the first result blindly, and a fallback IS
a result: Pelias answers an address it cannot resolve with its own "United
Kingdom" placeholder (match_type="fallback", layer="country", confidence 0.1),
always at the same point. Google does the same with a country-typed result.

Everything downstream was then computed from Cumbria: nearest station Lazonby
(LZB), no rail fare LZB→EUS, a school walk measured from the wrong county — and
the provenance was internally consistent about it, which is why it read as a
fare-data gap rather than a geocoding one.

The fixtures are the fields the parsers read. The full responses are in
``data/api_cache/`` (e.g. 377fccc69281fb70c8d6dccd2cfc7451.json, the query
"Mortimer St. John's C.E. Infant School, The Street, Mortimer Common, Reading").
"""

from __future__ import annotations

from houses.apis.google_geocode_api import GoogleGeocodeApi, _GoogleResponse
from houses.apis.ors_api import ORSApi, _PeliasResponse

# The point Pelias returns for a real Didcot match, and the point it returns
# for everything it cannot resolve (its "United Kingdom" extent centre).
DIDCOT_LAT, DIDCOT_LON = 51.6102143, -1.2528258
PELIAS_FALLBACK_LAT, PELIAS_FALLBACK_LON = 54.75844, -2.69531


def test_pelias_fallback_is_not_a_location():
    fallback: _PeliasResponse = {
        "features": [
            {
                "geometry": {"coordinates": [PELIAS_FALLBACK_LON, PELIAS_FALLBACK_LAT]},
                "properties": {
                    "name": "United Kingdom",
                    "country": "United Kingdom",
                    "layer": "country",
                    "confidence": 0.1,
                    "accuracy": "centroid",
                    "match_type": "fallback",
                },
            }
        ],
    }

    assert ORSApi._first_usable_feature(fallback) is None


def test_pelias_match_is_a_location():
    match: _PeliasResponse = {
        "features": [
            {
                "geometry": {"coordinates": [DIDCOT_LON, DIDCOT_LAT]},
                "properties": {"name": "Foxhall Road", "layer": "street", "match_type": "exact"},
            }
        ],
    }

    point = ORSApi._first_usable_feature(match)
    assert point is not None
    assert (point.lat, point.lon) == (DIDCOT_LAT, DIDCOT_LON)


def test_pelias_empty_features_is_not_a_location():
    assert ORSApi._first_usable_feature(_PeliasResponse(features=[])) is None


def test_google_country_result_is_not_a_location():
    """Google's unresolvable-address answer: the country, at the GB centroid."""
    country: _GoogleResponse = {
        "results": [
            {
                "formatted_address": "United Kingdom",
                "types": ["country", "political"],
                "geometry": {"location": {"lat": PELIAS_FALLBACK_LAT, "lng": PELIAS_FALLBACK_LON}},
            }
        ],
    }

    assert GoogleGeocodeApi._first_usable_result(country) is None


def test_google_street_result_is_a_location():
    street: _GoogleResponse = {
        "results": [
            {
                "formatted_address": "Foxhall Rd, Didcot OX11 7AB, UK",
                "types": ["route"],
                "geometry": {"location": {"lat": DIDCOT_LAT, "lng": DIDCOT_LON}},
            }
        ],
    }

    point = GoogleGeocodeApi._first_usable_result(street)
    assert point is not None
    assert (point.lat, point.lon) == (DIDCOT_LAT, DIDCOT_LON)


def test_google_empty_results_is_not_a_location():
    assert GoogleGeocodeApi._first_usable_result(_GoogleResponse(results=[])) is None
