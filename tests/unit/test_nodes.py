from __future__ import annotations

import json

import pytest

from houses.geo import GeoPoint
from houses.model.geo import _deserialize_gp, is_single_property_address


class TestSinglePropertyAddress:
    """Tests for is_single_property_address helper used by BestLocationNode."""

    def test_detects_single_property(self):
        assert is_single_property_address("31 Isambard Road, Southall UB2 4GN") is True
        assert is_single_property_address("London") is False
        assert is_single_property_address("10 High Street, London SW1V 2QQ") is True
        assert is_single_property_address("Maidenhead") is False
        assert is_single_property_address("") is False
        assert is_single_property_address(None) is False


class TestSerializeGP:
    """Tests for GeoPoint serialization helpers used by the DAG persistence layer."""

    def test_roundtrip(self):
        """GeoPoint serialized via dag.persistence._serialize_value produces correct lat/lon in JSON."""
        from dag.persistence import _serialize_value

        gp = GeoPoint(lat=51.5, lon=-0.1)
        s = _serialize_value(gp)
        d = json.loads(s)
        assert d["lat"] == 51.5
        assert d["lon"] == -0.1

    def test_deserialize(self):
        gp = _deserialize_gp('{"lat": 51.5, "lon": -0.1}')
        assert gp == GeoPoint(lat=51.5, lon=-0.1)

    def test_invalid_json_returns_none(self):
        assert _deserialize_gp("not-json") is None
