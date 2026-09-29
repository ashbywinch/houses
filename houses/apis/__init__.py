"""One class per external API — the ONLY thing callers touch.

No caller here knows HTTP exists. Each API class encapsulates:

* its **settings** (which env key, what format),
* its **state** (the pacing + daily-quota gate for its profile),
* its **actual API details** (endpoint URLs, wire shapes, auth headers,
  response parsing into named domain types).

A caller does ``minutes = await ors.directions(origin, station)`` and gets
a domain value back. The mechanics live in :mod:`houses.apigw` (the shared
transport: cache, pace, quota, typed errors); these classes are the thin
domain layer over it — and the place an API's quirks live, once.

Adding an API = one class, one profile constant in :mod:`houses.apigw`,
one module-level instance. Callers then import the instance and call its
methods.

Request wire shapes are plain dataclasses: the transport serializes them
via ``dataclasses.asdict`` (nested shapes included), so API classes carry
the field names as documentation and never hand-build response dicts.
Provider payloads are parsed INSIDE the class into named response types.

Each API class lives in its own module (coding-standards: one class per
module); this package init is the public surface: the instances callers
import, plus the types serialized/consumed across modules.
"""

from __future__ import annotations

from houses.apis.epc import EpcApi, EpcSearchResult
from houses.apis.google_geocode import GoogleGeocodeApi, GoogleGeocodeParams
from houses.apis.google_places import (
    GooglePlacesApi,
    PlacesCenter,
    PlacesCircle,
    PlacesRestriction,
    PlacesResult,
    PlacesSearchBody,
)
from houses.apis.nominatim import NominatimApi, NominatimParams
from houses.apis.ors import ORSApi, OrsDirectionsBody, OrsReverseParams, OrsSearchParams
from houses.apis.overpass import OverpassApi, OverpassElement, OverpassParams, OverpassResponse
from houses.apis.postcodesapi import OUTCODE_RE, PostcodesApi
from houses.apis.transport import BaseApi, FetchArgs

ors = ORSApi()
nominatim = NominatimApi()
google_geocode = GoogleGeocodeApi()
places = GooglePlacesApi()
postcodes = PostcodesApi()
epc = EpcApi()
overpass = OverpassApi()

__all__ = [
    "BaseApi",
    "EpcApi",
    "EpcSearchResult",
    "FetchArgs",
    "GoogleGeocodeApi",
    "GoogleGeocodeParams",
    "GooglePlacesApi",
    "NominatimApi",
    "NominatimParams",
    "ORSApi",
    "OUTCODE_RE",
    "OrsDirectionsBody",
    "OrsReverseParams",
    "OrsSearchParams",
    "OverpassApi",
    "OverpassElement",
    "OverpassParams",
    "OverpassResponse",
    "PlacesCenter",
    "PlacesCircle",
    "PlacesRestriction",
    "PlacesResult",
    "PlacesSearchBody",
    "PostcodesApi",
    "epc",
    "google_geocode",
    "nominatim",
    "ors",
    "overpass",
    "places",
    "postcodes",
]
