"""Rightmove drawn-area search URLs.

Spike result (2026-08-02, verified live on rightmove.co.uk): drawn search areas
are encoded as ``locationIdentifier=USERDEFINEDAREA^{"polylines":"<enc>"}`` where
``<enc>`` is the Google Maps polyline algorithm at 1e5 precision — NOT a plain
lat/lon list. A URL carrying that parameter renders the polygon on the map and
returns the properties inside it.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit


class RightmoveSearch:
    """Constants of the Rightmove drawn-area search URLs and the Google
    polyline codec (review-log: one holder for the URL + spec constants the
    module's pure functions read)."""

    SEARCH_BASE = "https://www.rightmove.co.uk/property-for-sale/map.html"
    LOCATION_PREFIX = 'USERDEFINEDAREA^{"polylines":"'
    LOCATION_SUFFIX = '"}'
    POLYLINE_PRECISION = 1e5  # Google polyline: 5 decimal places (spec)
    POLYLINE_CONTINUATION = 0x20  # chunk continuation bit (spec)
    POLYLINE_CHUNK_MASK = 0x1F  # low 5 bits per chunk (spec)
    POLYLINE_CHUNK_BITS_COUNT = 5  # bits consumed per chunk (spec)
    POLYLINE_ASCII_OFFSET = 63  # ASCII-63 offset of emitted chunk bytes (spec)


type Coord = tuple[float, float]


def encode_polyline(coords: list[Coord]) -> str:
    """Encode a list of (lat, lon) pairs with the Google polyline algorithm."""
    out: list[str] = []
    prev_lat = prev_lon = 0
    for lat, lon in coords:
        lat5, lon5 = round(lat * RightmoveSearch.POLYLINE_PRECISION), round(lon * RightmoveSearch.POLYLINE_PRECISION)
        dlat, dlon = lat5 - prev_lat, lon5 - prev_lon
        prev_lat, prev_lon = lat5, lon5
        for v in (dlat, dlon):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= RightmoveSearch.POLYLINE_CONTINUATION:
                out.append(
                    chr(
                        (RightmoveSearch.POLYLINE_CONTINUATION | (v & RightmoveSearch.POLYLINE_CHUNK_MASK))
                        + RightmoveSearch.POLYLINE_ASCII_OFFSET
                    )
                )
                v >>= RightmoveSearch.POLYLINE_CHUNK_BITS_COUNT
            out.append(chr(v + RightmoveSearch.POLYLINE_ASCII_OFFSET))
    return "".join(out)


def decode_polyline(encoded: str) -> list[Coord]:
    """Decode a Google polyline string back into (lat, lon) pairs."""
    coords: list[Coord] = []
    lat = lon = 0
    i = 0
    while i < len(encoded):
        for is_lat in (True, False):
            shift = result = 0
            while True:
                b = ord(encoded[i]) - RightmoveSearch.POLYLINE_ASCII_OFFSET
                i += 1
                result |= (b & RightmoveSearch.POLYLINE_CHUNK_MASK) << shift
                shift += RightmoveSearch.POLYLINE_CHUNK_BITS_COUNT
                if b < RightmoveSearch.POLYLINE_CONTINUATION:
                    break
            d = ~(result >> 1) if result & 1 else result >> 1
            if is_lat:
                lat += d
            else:
                lon += d
        scaled_lat = lat / RightmoveSearch.POLYLINE_PRECISION
        scaled_lon = lon / RightmoveSearch.POLYLINE_PRECISION
        coords.append((scaled_lat, scaled_lon))
    return coords


def _closed(coords: list[Coord]) -> list[Coord]:
    """Return the loop closed — first point repeated at the end (as Rightmove does)."""
    return coords if coords[0] == coords[-1] else [*coords, coords[0]]


def location_identifier(coords: list[Coord]) -> str:
    """Build the ``locationIdentifier`` value for a drawn-area search."""
    return RightmoveSearch.LOCATION_PREFIX + encode_polyline(_closed(coords)) + RightmoveSearch.LOCATION_SUFFIX


def build_search_url(
    coords: list[Coord],
    *,
    min_beds: int | None = None,
    property_type: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
) -> str:
    """Build a Rightmove map search URL for the drawn polygon."""
    params = {
        **{
            "searchType": "MAP",
            "insId": "1",
            "radius": "0.0",
            "maxBedrooms": "",
            "maxDaysSinceAdded": "",
            "_includeLetAgreed": "on",
            "sortBy": "6",
            "includeSSTC": "true",
            "viewType": "MAP",
            "channel": "BUY",
            "index": "0",
        },
        "locationIdentifier": location_identifier(coords),
        "minPrice": "" if min_price is None else str(min_price),
        "maxPrice": "" if max_price is None else str(max_price),
        "minBedrooms": "" if min_beds is None else str(min_beds),
        "displayPropertyType": property_type or "",
    }
    return RightmoveSearch.SEARCH_BASE + "?" + urlencode(params)


def parse_search_url(url: str) -> list[Coord]:
    """Extract the polygon coordinates from a Rightmove search URL."""
    qs = parse_qs(urlsplit(url).query)
    lid = qs["locationIdentifier"][0]
    if not (lid.startswith(RightmoveSearch.LOCATION_PREFIX) and lid.endswith(RightmoveSearch.LOCATION_SUFFIX)):
        raise ValueError(f"not a drawn-area locationIdentifier: {lid[:80]!r}")
    return decode_polyline(lid[len(RightmoveSearch.LOCATION_PREFIX) : -len(RightmoveSearch.LOCATION_SUFFIX)])
