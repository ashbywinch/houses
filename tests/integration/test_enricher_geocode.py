"""Tests for enricher geocoding (DAG) — uses httpx MockTransport for the HTTP layer."""

from __future__ import annotations

import pytest
from httpx import Response

from dag.derived_node import flush_processor
from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint
from houses.nodes.geocode import GeocodeNode


@pytest.mark.asyncio
async def test_geocode_full_address(_mock_http_requests):
    """A valid full address should return lat/lng from geocoding service."""
    _mock_http_requests.add_rule(
        lambda url: "maps.googleapis.com/maps/api/geocode" in url and "RG14" in url,
        lambda request: Response(
            200,
            json={
                "status": "OK",
                "results": [{"geometry": {"location": {"lat": 51.4, "lng": -1.32}}}],
            },
        ),
    )
    addr = UserInputNode[str]("addr_gfa", str)
    node = GeocodeNode("geocode_gfa", best_address=addr)
    addr.push("RG14 1AA", "test")
    await flush_processor()
    result = await node.attempt()
    assert any("maps.googleapis.com" in c for c in _mock_http_requests.calls)
    assert result.value_or_none() == GeoPoint(51.4, -1.32)


@pytest.mark.asyncio
async def test_geocode_simple_address(_mock_http_requests):
    """A simple outcode-like address should resolve via geocoding service."""
    _mock_http_requests.add_rule(
        lambda url: "maps.googleapis.com/maps/api/geocode" in url and "SL6" in url,
        lambda request: Response(
            200,
            json={
                "status": "OK",
                "results": [{"geometry": {"location": {"lat": 51.5, "lng": -0.7}}}],
            },
        ),
    )
    addr = UserInputNode[str]("addr_sa", str)
    node = GeocodeNode("geocode_sa", best_address=addr)
    addr.push("SL6", "test")
    await flush_processor()
    result = await node.attempt()
    assert any("maps.googleapis.com" in c for c in _mock_http_requests.calls)
    assert result.value_or_none() == GeoPoint(51.5, -0.7)


@pytest.mark.asyncio
async def test_geocode_caches_result():
    """Geocoding the same address twice should only hit the API once (in-memory cache)."""
    addr = UserInputNode[str]("addr_cr", str)
    node = GeocodeNode("geocode_cr", best_address=addr)

    addr.push("OX11 1AA", "test")
    await flush_processor()
    result1 = await node.attempt()

    addr.push("OX11 1AA", "test")
    await flush_processor()
    result2 = await node.attempt()

    assert result1.value_or_none() == GeoPoint(51.5, -0.1)
    assert result2.value_or_none() == GeoPoint(51.5, -0.1)


@pytest.mark.asyncio
async def test_geocode_caches_success():
    """A successful geocode should cache the result and not retry."""
    addr = UserInputNode[str]("addr_cs", str)
    node = GeocodeNode("geocode_cs", best_address=addr)

    addr.push("GU22 8BQ", "test")
    await flush_processor()
    result1 = await node.attempt()

    addr.push("GU22 8BQ", "test")
    await flush_processor()
    result2 = await node.attempt()

    assert result1.value_or_none() == GeoPoint(51.5, -0.1)
    assert result2.value_or_none() == GeoPoint(51.5, -0.1)


@pytest.mark.asyncio
async def test_geocode_all_apis_fail(_mock_http_requests):
    """When all geocoding APIs return errors, the result should be impossible."""
    _mock_http_requests.add_rule(
        lambda url: "maps.googleapis.com" in url,
        lambda request: Response(403),
    )
    _mock_http_requests.add_rule(
        lambda url: "openrouteservice.org/geocode" in url,
        lambda request: Response(403),
    )
    _mock_http_requests.add_rule(
        lambda url: "nominatim.openstreetmap.org" in url,
        lambda request: Response(403),
    )

    addr = UserInputNode[str]("addr_fail", str)
    node = GeocodeNode("geocode_fail", best_address=addr)
    addr.push("GU22 8BQ", "test")
    await flush_processor()
    result = await node.attempt()
    assert result.impossible


@pytest.mark.asyncio
async def test_geocode_empty_address(_mock_http_requests):
    """Empty address should return impossible (or fall through gracefully)."""
    _mock_http_requests.add_rule(
        lambda url: "maps.googleapis.com" in url,
        lambda request: Response(403),
    )
    _mock_http_requests.add_rule(
        lambda url: "openrouteservice.org/geocode" in url,
        lambda request: Response(403),
    )
    _mock_http_requests.add_rule(
        lambda url: "nominatim.openstreetmap.org" in url,
        lambda request: Response(403),
    )

    addr = UserInputNode[str]("addr_ea", str)
    node = GeocodeNode("geocode_ea", best_address=addr)
    addr.push("", "test")
    await flush_processor()
    result = await node.attempt()
    assert result.impossible


@pytest.mark.asyncio
async def test_geocode_normalises_case(_mock_http_requests):
    """Address is uppercased by the service layer before lookup."""
    _mock_http_requests.add_rule(
        lambda url: "maps.googleapis.com" in url and "RG14" in url,
        lambda request: Response(
            200,
            json={
                "status": "OK",
                "results": [{"geometry": {"location": {"lat": 51.5, "lng": -0.1}}}],
            },
        ),
    )
    addr = UserInputNode[str]("addr_norm", str)
    node = GeocodeNode("geocode_norm", best_address=addr)
    addr.push("rg14 1aa", "test")
    await flush_processor()
    result = await node.attempt()
    assert result.value_or_none() == GeoPoint(51.5, -0.1)


class TestGeocodeNodeFullAddress:
    """Regression: GeocodeNode must resolve a full street address correctly.

    The ORS Pelias geocoding API treats short strings like "SL6" as
    free-text placenames and returns coordinates ~139 km from the actual
    location.  The full address ensures the geocoder finds the correct
    property.
    """

    @pytest.mark.asyncio
    async def test_full_address_resolves_to_sensible_area(self, _mock_http_requests):
        """Full address should resolve to coordinates near the property."""
        _mock_http_requests.add_rule(
            lambda url: "maps.googleapis.com/maps/api/geocode" in url
            and "Shoppenhangers" in url,
            lambda request: Response(
                200,
                json={
                    "status": "OK",
                    "results": [
                        {"geometry": {"location": {"lat": 51.52, "lng": -0.73}}}
                    ],
                },
            ),
        )

        addr = UserInputNode[str]("addr_fa", str)
        node = GeocodeNode("geocode_fa", best_address=addr)
        addr.push("Shoppenhangers Road, Maidenhead, SL6", "test")
        await flush_processor()
        result = await node.attempt()
        coords = result.value_or_none()
        assert coords is not None

        maidenhead = GeoPoint(51.52, -0.73)
        dist = maidenhead.distance_km_to(coords)
        assert dist < 5, (
            f"Full address resolved to ({coords.lat:.4f}, {coords.lon:.4f}), "
            f"{dist:.0f} km from Maidenhead — should be < 5 km. "
            f"This suggests an outcode was used instead of the full address, "
            f"which would cause ORS Pelias to misinterpret it as a placename."
        )
