"""A geocoder's "no idea" answer must never become a property's location.

2026-09-28: "Foxhall Road, Didcot, OX11" was stored at 54.75844,-2.69531 — a
Pelias fallback. Both parsers took the first place blindly, and a fallback IS a
place: Pelias answers an address it cannot resolve with its own "United Kingdom"
placeholder (match_type="fallback", layer="country", confidence 0.1), always at
the same point. Google does the same with a country-typed result.

Everything downstream was then computed from Cumbria: nearest station Lazonby
(LZB), no rail fare LZB→EUS, a school walk measured from the wrong county — and
the provenance was internally consistent about it, which is why it read as a
fare-data gap rather than a geocoding one. 91 cached Pelias responses carry that
fallback.

The fixtures are the fields the answer classes read. The full responses are in
``data/api_cache/`` (e.g. 377fccc69281fb70c8d6dccd2cfc7451.json, the query
"Mortimer St. John's C.E. Infant School, The Street, Mortimer Common, Reading").
"""

from __future__ import annotations

from houses.apis.google_geocode_result import GoogleGeocodeResult
from houses.apis.pelias_search_result import PeliasSearchResult
from houses.geopoint import GeoPoint

# The point Pelias returns for a real Didcot match, and the point it returns
# for everything it cannot resolve (its "United Kingdom" extent centre).
DIDCOT = GeoPoint(lat=51.6102143, lon=-1.2528258)
PELIAS_FALLBACK = GeoPoint(lat=54.75844, lon=-2.69531)


def test_pelias_fallback_is_not_a_location():
    answer = PeliasSearchResult.from_response(
        {
            "features": [
                {
                    "geometry": {"coordinates": [PELIAS_FALLBACK.lon, PELIAS_FALLBACK.lat]},
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
    )

    assert answer.location() is None


def test_pelias_match_is_a_location():
    answer = PeliasSearchResult.from_response(
        {
            "features": [
                {
                    "geometry": {"coordinates": [DIDCOT.lon, DIDCOT.lat]},
                    "properties": {"name": "Foxhall Road", "layer": "street", "match_type": "exact"},
                }
            ],
        }
    )

    assert answer.location() == DIDCOT


def test_pelias_skips_a_fallback_before_a_real_place():
    """Features come best-first; a fallback anywhere in them is not a place."""
    answer = PeliasSearchResult.from_response(
        {
            "features": [
                {
                    "geometry": {"coordinates": [PELIAS_FALLBACK.lon, PELIAS_FALLBACK.lat]},
                    "properties": {"match_type": "fallback"},
                },
                {
                    "geometry": {"coordinates": [DIDCOT.lon, DIDCOT.lat]},
                    "properties": {"match_type": "exact"},
                },
            ],
        }
    )

    assert answer.location() == DIDCOT


def test_pelias_without_features_is_not_a_location():
    assert PeliasSearchResult.from_response({"features": []}).location() is None
    assert PeliasSearchResult.from_response(None).location() is None


def test_google_country_result_is_not_a_location():
    """Google's unresolvable-address answer: the country, at the GB centroid."""
    answer = GoogleGeocodeResult.from_response(
        {
            "results": [
                {
                    "formatted_address": "United Kingdom",
                    "types": ["country", "political"],
                    "geometry": {"location": {"lat": PELIAS_FALLBACK.lat, "lng": PELIAS_FALLBACK.lon}},
                }
            ],
        }
    )

    assert answer.location() is None


def test_google_street_result_is_a_location():
    answer = GoogleGeocodeResult.from_response(
        {
            "results": [
                {
                    "formatted_address": "Foxhall Rd, Didcot OX11 7AB, UK",
                    "types": ["route"],
                    "geometry": {"location": {"lat": DIDCOT.lat, "lng": DIDCOT.lon}},
                }
            ],
        }
    )

    assert answer.location() == DIDCOT


def test_google_without_results_is_not_a_location():
    assert GoogleGeocodeResult.from_response({"results": []}).location() is None
    assert GoogleGeocodeResult.from_response(None).location() is None


def test_pelias_town_name_is_the_locality():
    answer = PeliasSearchResult.from_response(
        {"features": [{"properties": {"locality": "Didcot", "layer": "locality"}}]}
    )

    assert answer.town_name() == "Didcot"


def test_pelias_town_name_falls_back_to_the_borough():
    """For a point in London the settlement is the borough, not a locality."""
    answer = PeliasSearchResult.from_response(
        {"features": [{"properties": {"borough": "Merton", "layer": "borough"}}]}
    )

    assert answer.town_name() == "Merton"


def test_pelias_town_name_is_none_for_a_fallback():
    answer = PeliasSearchResult.from_response(
        {
            "features": [
                {
                    "properties": {
                        "name": "United Kingdom",
                        "locality": None,
                        "layer": "country",
                        "match_type": "fallback",
                    }
                }
            ]
        }
    )

    assert answer.town_name() is None
    assert PeliasSearchResult.from_response({"features": []}).town_name() is None
