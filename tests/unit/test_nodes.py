from __future__ import annotations

import json

import pytest

from houses.geo import GeoPoint
from houses.model.geo import serialize_gp
from houses.model import NodeKind
from houses.model.property import best_address, best_location, map_url
from houses.model.registry import NODES, get_node
import houses.model.rightmove  # noqa: F401 — registers Rightmove nodes


class TestNodeRegistry:
    def test_all_base_nodes_registered(self):
        expected = {
            "rid",
            "rightmove_url",
            "rightmove_address",
            "rightmove_bedrooms",
            "rightmove_price",
            "rightmove_location",
            "geocode_location",
            "corrected_address",
            "precise_location",
            "best_address",
            "best_location",
            "map_url",
        }
        registered = set(NODES.keys())
        assert expected == registered, f"Difference: expected={expected - registered}, extra={registered - expected}"

    def test_derived_nodes_have_compute(self):
        for nid in ("best_address", "best_location", "map_url"):
            nd = get_node(nid)
            assert nd.kind == NodeKind.derived
            assert nd.compute is not None

    def test_source_nodes_no_compute(self):
        for nid in ("rid", "rightmove_url", "rightmove_address", "rightmove_location", "geocode_location"):
            nd = get_node(nid)
            assert nd.kind == NodeKind.source

    def test_user_input_nodes_have_table(self):
        for nid in ("corrected_address", "precise_location"):
            nd = get_node(nid)
            assert nd.kind == NodeKind.user_input
            assert nd.user_table is not None

    def test_get_unknown_node(self):
        with pytest.raises(KeyError):
            get_node("nonexistent")


class TestBestAddress:
    def test_corrected_takes_priority(self):
        result = best_address(corrected_address="User Address", rightmove_address="RM Address")
        assert result == ("User Address", "User correction")

    def test_fallback_to_rightmove(self):
        result = best_address(corrected_address=None, rightmove_address="RM Address")
        assert result == ("RM Address", "Rightmove")

    def test_both_none(self):
        result = best_address(corrected_address=None, rightmove_address=None)
        assert result == (None, "")


class TestBestLocation:
    async def test_precise_takes_priority(self):
        gp = GeoPoint(lat=51.5, lon=-0.1)
        result = await best_location(
            precise_location=gp, rightmove_location=None, best_address="10 High St",
        )
        assert result == (gp, "User location")

    async def test_geocode_preferred_over_rightmove_for_single_property(self):
        rm = GeoPoint(lat=51.4, lon=-0.2)
        gc = GeoPoint(lat=51.5, lon=-0.37)

        async def fake_geocode(_):
            from dag.attempt import Attempt
            return Attempt.succeeded(gc)

        result, source = await best_location(
            precise_location=None, rightmove_location=rm,
            best_address="31 Isambard Road, Southall UB2 4GN",
            _geocoder=fake_geocode,
        )
        assert result == gc
        assert source == "Geocoded"

    async def test_rightmove_used_when_address_is_vague(self):
        rm = GeoPoint(lat=51.4, lon=-0.2)
        result, source = await best_location(
            precise_location=None, rightmove_location=rm, best_address="London",
        )
        assert result == rm
        assert source == "Rightmove map"

    async def test_precise_from_json_string(self):
        gp = GeoPoint(lat=51.5, lon=-0.1)
        json_str = serialize_gp(gp)
        result = await best_location(
            precise_location=json_str, rightmove_location=None, best_address=None,
        )
        assert result == (gp, "User location")

    async def test_none_when_no_data(self):
        result = await best_location(precise_location=None, rightmove_location=None, best_address=None)
        assert result == (None, "")

    async def test_geocodes_single_property_address_when_no_other_source(self):
        async def fake_geocode(address: str):
            from dag.attempt import Attempt
            return Attempt.succeeded(GeoPoint(51.5, -0.37))

        result, source = await best_location(
            precise_location=None, rightmove_location=None,
            best_address="31 Isambard Road, Southall UB2 4GN",
            _geocoder=fake_geocode,
        )
        assert result == GeoPoint(51.5, -0.37)
        assert source == "Geocoded"

    async def test_does_not_geocode_vague_address(self):
        result, source = await best_location(
            precise_location=None, rightmove_location=None,
            best_address="London",
            _geocoder=None,
        )
        assert result is None
        assert source == ""

    async def test_skips_geocoding_when_precise_exists(self):
        gp = GeoPoint(lat=51.5, lon=-0.1)
        result, source = await best_location(
            precise_location=gp, rightmove_location=None,
            best_address="31 Isambard Road, Southall UB2 4GN",
        )
        assert result == gp
        assert source == "User location"


class TestSinglePropertyAddress:
    def test_detects_single_property(self):
        from houses.model.geo import is_single_property_address

        assert is_single_property_address("31 Isambard Road, Southall UB2 4GN") is True
        assert is_single_property_address("London") is False
        assert is_single_property_address("10 High Street, London SW1V 2QQ") is True
        assert is_single_property_address("Maidenhead") is False
        assert is_single_property_address("") is False
        assert is_single_property_address(None) is False


class TestSerializeGP:
    def test_roundtrip(self):
        gp = GeoPoint(lat=51.5, lon=-0.1)
        from houses.model.persistence import _serialize_value

        s = _serialize_value(gp)
        d = json.loads(s)
        assert d["lat"] == 51.5
        assert d["lon"] == -0.1

    def test_deserialize(self):
        from houses.model.geo import _deserialize_gp
        gp = _deserialize_gp('{"lat": 51.5, "lon": -0.1}')
        assert gp == GeoPoint(lat=51.5, lon=-0.1)

    def test_invalid_json_returns_none(self):
        from houses.model.geo import _deserialize_gp
        assert _deserialize_gp("not-json") is None


class TestMapUrl:
    def test_valid_coords(self):
        result = map_url(best_location=GeoPoint(lat=51.5, lon=-0.1))
        assert result == ("https://www.google.com/maps?q=51.5,-0.1", "Computed")

    def test_none_location(self):
        result = map_url(best_location=None)
        assert result == (None, "")
