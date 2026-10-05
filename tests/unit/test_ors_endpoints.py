"""Every HeiGIT URL we call is the documented one, under the documented host.

2026-10-03: our ORS calls went to ``api.openrouteservice.org`` for months
after HeiGIT deprecated it (announced 2026-04-28; from 2026-08-27 that host
carries 10% of the plan's quota, and its usage is deliberately absent from
the account dashboard). A 403 "Quota exceeded" therefore looked like an
overuse bug while the dashboard showed a full pool — it was the *new* host's
pool, which we never called.

These pins are the migration table from the announcement; they exist so a
regression to the deprecated host, or a new endpoint added with a guessed
path, fails here instead of in production.
"""

from __future__ import annotations

from houses import ors_endpoints as endpoints
from houses.apis.ors import ORSApi
from houses.settings import settings

# https://ask.openrouteservice.org/t/deprecating-api-openrouteservice-org-in-favour-of-api-heigit-org/7912
DEPRECATED_HOST = "api.openrouteservice.org"


def test_the_host_is_the_documented_one():
    assert settings.ors_base_url == "https://api.heigit.org"


def test_paths_follow_the_documented_structure():
    """api.heigit.org/<service>/<version>/ — note geocoding is its own
    service (Pelias), not a path under the openrouteservice prefix."""
    base = settings.ors_base_url
    assert f"{base}/openrouteservice/v2/directions" == endpoints.ORS_DIRECTIONS
    assert f"{base}/openrouteservice/v2/matrix" == endpoints.ORS_MATRIX
    assert f"{base}/pelias/v1/search" == endpoints.PELIAS_SEARCH
    assert f"{base}/pelias/v1/reverse" == endpoints.PELIAS_REVERSE


def test_the_api_class_uses_the_shared_endpoints():
    assert ORSApi.directions_url == endpoints.ORS_DIRECTIONS
    assert ORSApi.geocode_url == endpoints.PELIAS_SEARCH


def test_nothing_points_at_the_deprecated_host():
    for url in (
        endpoints.ORS_DIRECTIONS,
        endpoints.ORS_MATRIX,
        endpoints.PELIAS_SEARCH,
        endpoints.PELIAS_REVERSE,
    ):
        assert DEPRECATED_HOST not in url
