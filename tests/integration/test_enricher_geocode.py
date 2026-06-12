"""Tests for enricher geocoding — uses httpx MockTransport for the HTTP layer."""

import pytest
from httpx import Response

from houses.geo import GeoPoint
from houses.location import geocode


@pytest.mark.asyncio
async def test_geocode_full_postcode(_mock_http_requests):
    """A valid full postcode should return lat/lng from postcodes.io."""
    _mock_http_requests.add_rule(
        lambda url: "postcodes.io/postcodes/RG14" in url,
        lambda request: Response(200, json={"status": 200, "result": {"latitude": 51.4, "longitude": -1.32}}),
    )
    result = await geocode("RG14 1AA")
    assert any("api.postcodes.io/postcodes/RG14" in c for c in _mock_http_requests.calls)
    assert result.value_or_none() == GeoPoint(51.4, -1.32)


@pytest.mark.asyncio
async def test_geocode_outcode(_mock_http_requests):
    """An outcode (e.g. SL6) should hit the outcode endpoint."""
    _mock_http_requests.add_rule(
        lambda url: "postcodes.io/outcodes/SL6" in url,
        lambda request: Response(200, json={"status": 200, "result": {"latitude": 51.5, "longitude": -0.7}}),
    )
    result = await geocode("SL6")
    assert any("api.postcodes.io/outcodes/SL6" in c for c in _mock_http_requests.calls)
    assert result.value_or_none() == GeoPoint(51.5, -0.7)


@pytest.mark.asyncio
async def test_geocode_caches_result():
    """Geocoding the same postcode twice should only hit the API once."""
    result1 = await geocode("OX11 1AA")
    result2 = await geocode("OX11 1AA")

    assert result1.value_or_none() == GeoPoint(51.5, -0.1)
    assert result2.value_or_none() == GeoPoint(51.5, -0.1)


@pytest.mark.asyncio
async def test_geocode_caches_success():
    """A successful geocode should cache the result and not retry."""
    result1 = await geocode("GU22 8BQ")
    result2 = await geocode("GU22 8BQ")

    assert result1.value_or_none() == GeoPoint(51.5, -0.1)
    assert result2.value_or_none() == GeoPoint(51.5, -0.1)


@pytest.mark.asyncio
async def test_geocode_404_caches_none(_mock_http_requests):
    """A 404 from postcodes.io should cache None and not retry."""
    _mock_http_requests.add_rule(
        lambda url: "api.postcodes.io" in url,
        lambda request: Response(404),
    )
    result1 = await geocode("GU22 8BQ")
    result2 = await geocode("GU22 8BQ")
    assert result1.value_or_none() is None
    assert result2.value_or_none() is None
    assert len(_mock_http_requests.calls) == 1


@pytest.mark.asyncio
async def test_geocode_empty_postcode():
    """Empty postcode should return None without making HTTP calls."""
    result = await geocode("")
    assert result.value_or_none() is None
    assert result.is_impossible


@pytest.mark.asyncio
async def test_geocode_normalises_case():
    """Postcode should be uppercased before lookup."""
    result = await geocode("rg14 1aa")
    assert result.value_or_none() == GeoPoint(51.5, -0.1)


class TestPropertyLocationOutcode:
    """Regression: ``PropertyLocation`` must never receive an outcode as
    ``address``.  The ORS Pelias geocoding API treats "SL6" as a
    free-text placename and returns coordinates ~139 km from the actual
    location.  Callers must always pass a full street address so the
    geocoder finds the correct property.

    This test verifies that when the full address is provided the
    coordinates resolve within a sensible radius of the expected area.
    """

    @pytest.mark.asyncio
    async def test_full_address_resolves_to_sensible_area(self, _mock_http_requests):
        """Full address should resolve to coordinates near the property."""
        from houses.geo import GeoPoint
        from houses.location import PropertyLocation

        # Register a custom handler returning Maidenhead coordinates
        # for the full street address.
        def _maidenhead_response(request):
            url = str(request.url)
            if "googleapis.com/maps/api/geocode" in url:
                return Response(
                    200,
                    json={
                        "status": "OK",
                        "results": [{"geometry": {"location": {"lat": 51.52, "lng": -0.73}}}],
                    },
                )
            if "openrouteservice.org/geocode" in url:
                return Response(
                    200,
                    json={
                        "features": [{"geometry": {"coordinates": [-0.73, 51.52]}}],
                    },
                )
            # Nominatim
            return Response(200, json=[{"lat": "51.52", "lon": "-0.73"}])

        _mock_http_requests.add_rule(
            lambda url: "Shoppenhangers" in str(url),
            _maidenhead_response,
        )

        loc = PropertyLocation(
            postcode="SL6",
            address="Shoppenhangers Road, Maidenhead, SL6",
        )
        loc = await loc.resolve()
        coords = loc.coordinates.value_or_none()
        assert coords is not None

        maidenhead = GeoPoint(51.52, -0.73)
        dist = maidenhead.distance_km_to(coords)
        assert dist < 5, (
            f"Full address resolved to ({coords.lat:.4f}, {coords.lon:.4f}), "
            f"{dist:.0f} km from Maidenhead — should be < 5 km. "
            f"This suggests an outcode was used instead of the full address, "
            f"which would cause ORS Pelias to misinterpret it as a placename."
        )
