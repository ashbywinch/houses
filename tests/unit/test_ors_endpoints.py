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

import httpx
import pytest

from houses import apigw
from houses import ors_endpoints as endpoints
from houses.apis.ors import SETTLEMENT_LAYERS, ORSApi
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


class _TransientClient:
    """httpx-shaped fake: a 429 (never cached — the unit cache stays clean)
    that records the request params it was handed."""

    def __init__(self) -> None:
        self.params: dict[str, object] = {}

    async def __aenter__(self) -> _TransientClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def request(
        self, method: str, url: str, *args: object, **kwargs: object
    ) -> httpx.Response:
        sent = kwargs.get("params")
        if isinstance(sent, dict):
            self.params.update(sent)
        return httpx.Response(429, request=httpx.Request(method, url))


@pytest.mark.asyncio
async def test_reverse_geocode_asks_for_settlements_not_streets():
    """Unconstrained, Pelias reverse answers with the closest feature of any
    kind — a house's own street, i.e. a 0-minute walk, which walkability's
    plausibility gate rejects: houses whose address-derived town is a district
    name (London, South Oxfordshire) silently lost walk_to_town. Ask for
    settlements."""
    client = _TransientClient()
    with pytest.raises(apigw.GatewayHttpError):
        await ORSApi().reverse_geocode(51.5, -0.1, _client_factory=lambda **k: client)
    assert client.params, "no request params captured — the assertions below would be vacuous"
    assert client.params.get("layers") == SETTLEMENT_LAYERS
    assert "locality" in str(client.params.get("layers"))
    assert "street" not in str(client.params.get("layers"))
    assert "address" not in str(client.params.get("layers"))


def test_a_trailing_slash_on_the_base_urls_is_normalised():
    """Both bases are joined with a leading-slash path, so a configured
    trailing slash would build a double slash (the gateway and ORS both 404
    on one). Normalised at the setting, so callers just concatenate."""
    from houses import ors_endpoints
    from houses.settings import Settings

    configured = Settings(
        _env_file=None,
        ors_base_url="https://api.heigit.org/",
        llm_base_url="https://gateway.example/v1/acct/gw/compat/",
    )
    assert configured.ors_base_url == "https://api.heigit.org"
    assert configured.llm_base_url == "https://gateway.example/v1/acct/gw/compat"
    assert "//openrouteservice" not in f"{configured.ors_base_url}/openrouteservice"
    assert ors_endpoints.ORS_DIRECTIONS.startswith("https://api.heigit.org/openrouteservice")
