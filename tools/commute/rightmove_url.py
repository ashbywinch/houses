"""Rightmove drawn-area search URLs.

Spike result (2026-08-02, verified live on rightmove.co.uk): drawn search areas
are encoded as ``locationIdentifier=USERDEFINEDAREA^{"polylines":"<enc>"}`` where
``<enc>`` is the Google Maps polyline algorithm at 1e5 precision — NOT a plain
lat/lon list. A URL carrying that parameter renders the polygon on the map and
returns the properties inside it.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit

SEARCH_BASE = "https://www.rightmove.co.uk/property-for-sale/map.html"
_LOCATION_PREFIX = 'USERDEFINEDAREA^{"polylines":"'
_LOCATION_SUFFIX = '"}'

Coord = tuple[float, float]


def encode_polyline(coords: list[Coord]) -> str:
    """Encode a list of (lat, lon) pairs with the Google polyline algorithm."""
    out: list[str] = []
    prev_lat = prev_lon = 0
    for lat, lon in coords:
        lat5, lon5 = round(lat * 1e5), round(lon * 1e5)
        dlat, dlon = lat5 - prev_lat, lon5 - prev_lon
        prev_lat, prev_lon = lat5, lon5
        for v in (dlat, dlon):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
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
                b = ord(encoded[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if result & 1 else result >> 1
            if is_lat:
                lat += d
            else:
                lon += d
        coords.append((lat / 1e5, lon / 1e5))
    return coords


def _closed(coords: list[Coord]) -> list[Coord]:
    """Return the loop closed — first point repeated at the end (as Rightmove does)."""
    return coords if coords[0] == coords[-1] else [*coords, coords[0]]


def location_identifier(coords: list[Coord]) -> str:
    """Build the ``locationIdentifier`` value for a drawn-area search."""
    return _LOCATION_PREFIX + encode_polyline(_closed(coords)) + _LOCATION_SUFFIX


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
        "searchType": "MAP",
        "locationIdentifier": location_identifier(coords),
        "insId": "1",
        "radius": "0.0",
        "minPrice": "" if min_price is None else str(min_price),
        "maxPrice": "" if max_price is None else str(max_price),
        "minBedrooms": "" if min_beds is None else str(min_beds),
        "maxBedrooms": "",
        "displayPropertyType": property_type or "",
        "maxDaysSinceAdded": "",
        "_includeLetAgreed": "on",
        "sortBy": "6",
        "includeSSTC": "true",
        "viewType": "MAP",
        "channel": "BUY",
        "index": "0",
    }
    return SEARCH_BASE + "?" + urlencode(params)


def parse_search_url(url: str) -> list[Coord]:
    """Extract the polygon coordinates from a Rightmove search URL."""
    qs = parse_qs(urlsplit(url).query)
    lid = qs["locationIdentifier"][0]
    if not (lid.startswith(_LOCATION_PREFIX) and lid.endswith(_LOCATION_SUFFIX)):
        raise ValueError(f"not a drawn-area locationIdentifier: {lid[:80]!r}")
    return decode_polyline(lid[len(_LOCATION_PREFIX) : -len(_LOCATION_SUFFIX)])
