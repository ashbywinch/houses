"""Rightmove drawn-area URL encoding — Google polyline + USERDEFINEDAREA location.

Spike-verified 2026-08-02 against rightmove.co.uk: a URL carrying
``locationIdentifier=USERDEFINEDAREA^{"polylines":"<enc>"}`` renders the polygon
on the map and returns properties inside it.
"""

from tools.commute.rightmove_url import (
    build_search_url,
    decode_polyline,
    encode_polyline,
    location_identifier,
    parse_search_url,
)

# Google's canonical polyline example (polylinealgorithm docs).
GOOGLE_EXAMPLE = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
GOOGLE_ENCODED = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"

# Rectangle around central London, captured live during the spike.
LONDON_RECT = [
    (51.5075, -0.1277),
    (51.5125, -0.1277),
    (51.5125, -0.1227),
    (51.5075, -0.1227),
    (51.5075, -0.1277),
]
LONDON_ENCODED = "{`kyHb}Wg^??g^f^??f^"


def test_encode_matches_google_reference():
    assert encode_polyline(GOOGLE_EXAMPLE) == GOOGLE_ENCODED


def test_encode_matches_spike_capture():
    assert encode_polyline(LONDON_RECT) == LONDON_ENCODED


def test_decode_round_trip():
    for coords in (GOOGLE_EXAMPLE, LONDON_RECT):
        assert decode_polyline(encode_polyline(coords)) == coords


def test_decode_matches_google_reference():
    assert decode_polyline(GOOGLE_ENCODED) == GOOGLE_EXAMPLE


def test_location_identifier_format():
    lid = location_identifier(LONDON_RECT)
    assert lid.startswith('USERDEFINEDAREA^{"polylines":"')
    assert lid.endswith('"}')
    assert LONDON_ENCODED in lid


def test_build_search_url_carries_encoded_location():
    url = build_search_url(LONDON_RECT, min_beds=2, property_type="houses")
    assert url.startswith("https://www.rightmove.co.uk/property-for-sale/map.html")
    assert "locationIdentifier=" in url
    assert "%5E%7B%22polylines%22" in url  # USERDEFINEDAREA^{"polylines":
    assert "minBedrooms=2" in url
    assert "displayPropertyType=houses" in url


def test_parse_search_url_round_trip():
    url = build_search_url(LONDON_RECT, min_beds=2, property_type="houses")
    assert parse_search_url(url) == LONDON_RECT


def test_loop_closed_automatically():
    open_rect = LONDON_RECT[:-1]
    assert location_identifier(open_rect) == location_identifier(LONDON_RECT)
    assert parse_search_url(build_search_url(open_rect)) == LONDON_RECT
