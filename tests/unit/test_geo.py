"""Tests for GeoPoint — coordinate distance calculation."""

from __future__ import annotations

from houses.geo import GeoPoint


class TestGeoPointDistance:
    """GeoPoint.distance_km_to — haversine great-circle distance."""

    def test_same_point_returns_zero(self):
        dist = GeoPoint(51.5, -0.13).distance_km_to(GeoPoint(51.5, -0.13))
        assert dist == 0.0

    def test_known_distance(self):
        dist = GeoPoint(51.5, -0.13).distance_km_to(GeoPoint(50.83, -0.14))
        assert 70 < dist < 80

    def test_symmetric(self):
        d1 = GeoPoint(51.5, -0.13).distance_km_to(GeoPoint(52.0, 0.0))
        d2 = GeoPoint(52.0, 0.0).distance_km_to(GeoPoint(51.5, -0.13))
        assert abs(d1 - d2) < 0.001
