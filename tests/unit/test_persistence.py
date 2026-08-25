"""Tests for dag.persistence — serialisation and DAG node result persistence.

Focuses on GeoPoint serialisation (not covered by the lower-level
tests/unit/dag/test_persistence.py) and property-id-patterned node results
adapted from the old houses.model.persistence test suite.

The ``_sqlite_memory`` autouse fixture in tests/unit/conftest.py sets up
an in-memory SQLite database for ``dag.persistence``, so every test
here runs isolated from the real database.
"""

from __future__ import annotations

import json

from dag.persistence import (
    _deserialize_value,
    _serialize_value,
    latest_node_result,
    save_node_result,
)
from houses.geopoint import GeoPoint

RID = "prop123"


def _serialized(value) -> str:
    """``_serialize_value`` narrowed to ``str`` — every value here serialises."""
    s = _serialize_value(value)
    assert s is not None
    return s


class TestGeoPointSerialisation:
    """GeoPoint serialisation/deserialisation via the helper functions."""

    def test_valid_geopoint_roundtrip(self):
        gp = GeoPoint(lat=51.5, lon=-0.1)
        s = _serialize_value(gp)
        loaded = _deserialize_value(s)
        assert loaded == gp
        assert isinstance(loaded, GeoPoint)

    def test_geopoint_serialises_with_type_markers(self):
        gp = GeoPoint(lat=51.5, lon=-0.1)
        s = _serialized(gp)
        d = json.loads(s)
        assert d["_type"] == "GeoPoint"
        assert d["_module"] == "houses.geopoint"
        assert d["lat"] == 51.5
        assert d["lon"] == -0.1

    def test_none_serialises_to_none(self):
        assert _serialize_value(None) is None

    def test_empty_string_deserialises_to_empty_string(self):
        assert _deserialize_value("") == ""

    def test_string_none_preserved(self):
        assert _deserialize_value("None") == "None"

    def test_empty_string_serialises_like_none(self):
        """Empty string serialises the same as None (bare empty string, not JSON-quoted)."""
        s = _serialize_value("")
        assert s == ""

    def test_quoted_empty_string_deserialises_to_empty(self):
        """Pre-serialised JSON empty string ``'""'`` round-trips to an empty string."""
        assert _deserialize_value('""') == ""

    def test_bool_passthrough(self):
        s = _serialize_value(True)
        assert _deserialize_value(s) is True

    def test_lat_lon_edge_cases(self):
        gp = GeoPoint(lat=0.0, lon=0.0)
        s = _serialize_value(gp)
        loaded = _deserialize_value(s)
        assert loaded == gp

    def test_geopoint_at_prime_meridian(self):
        gp = GeoPoint(lat=51.5, lon=0.0)
        s = _serialize_value(gp)
        loaded = _deserialize_value(s)
        assert loaded == gp


class TestGeoPointPersistence:
    """Full SQLite round-trip for GeoPoint values via save/latest_node_result."""

    def test_none_value_saves_and_loads_as_none(self):
        save_node_result(f"{RID}/best_location", {"value": None, "status": "succeeded"})
        loaded = latest_node_result(f"{RID}/best_location")
        assert loaded is not None
        assert loaded["value"] is None

    def test_geopoint_via_serialised_dict_roundtrips(self):
        """A serialised GeoPoint dict stored in a node result is
        deserialisable back to a GeoPoint with _deserialize_value."""
        gp = GeoPoint(lat=51.5, lon=-0.1)
        serialised = json.loads(_serialized(gp))
        save_node_result(
            f"{RID}/best_location",
            {"value": serialised, "status": "succeeded", "source": "manual"},
        )
        loaded = latest_node_result(f"{RID}/best_location")
        assert loaded is not None
        reconstructed = _deserialize_value(json.dumps(loaded["value"]))
        assert reconstructed == gp
        assert isinstance(reconstructed, GeoPoint)

    def test_geopoint_from_source_node_roundtrips(self):
        """GeoPoint stored under a ``rightmove_location`` node id loads back."""
        gp = GeoPoint(lat=51.5, lon=-0.1)
        serialised = json.loads(_serialized(gp))
        save_node_result(
            f"{RID}/rightmove_location",
            {"value": serialised, "status": "succeeded", "source": "rightmove_map"},
        )
        loaded = latest_node_result(f"{RID}/rightmove_location")
        assert loaded is not None
        reconstructed = _deserialize_value(json.dumps(loaded["value"]))
        assert reconstructed == gp

    def test_inline_none_string_survives(self):
        """When ``\"value\": \"None\"`` is stored in the result JSON directly,
        it comes back as the Python string ``\"None\"``, not Python None."""
        from datetime import UTC, datetime

        import dag.persistence as per

        per.init_db()
        conn = per._get_db()
        conn.execute(
            "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
            (
                f"{RID}/best_location",
                json.dumps({"value": "None", "status": "succeeded"}),
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
        loaded = latest_node_result(f"{RID}/best_location")
        assert loaded is not None
        assert loaded["value"] == "None"
        assert isinstance(loaded["value"], str)

    def test_non_geopoint_none_loads_as_none(self):
        """A plain string node with ``value: None`` loads back as None."""
        save_node_result(f"{RID}/best_address", {"value": None, "status": "succeeded"})
        loaded = latest_node_result(f"{RID}/best_address")
        assert loaded is not None
        assert loaded["value"] is None

    def test_two_geopoint_nodes_independent(self):
        """Multiple distinct node ids storing GeoPoints don't interfere."""
        gp_a = GeoPoint(lat=51.5, lon=-0.1)
        gp_b = GeoPoint(lat=52.0, lon=0.0)
        sa = json.loads(_serialized(gp_a))
        sb = json.loads(_serialized(gp_b))
        save_node_result(f"{RID}/first", {"value": sa, "status": "succeeded"})
        save_node_result(f"{RID}/second", {"value": sb, "status": "succeeded"})
        la = latest_node_result(f"{RID}/first")
        lb = latest_node_result(f"{RID}/second")
        assert la is not None and lb is not None
        ra = _deserialize_value(json.dumps(la["value"]))
        rb = _deserialize_value(json.dumps(lb["value"]))
        assert ra == gp_a
        assert rb == gp_b

    def test_latest_replaces_older(self):
        """Saving a new node result for the same id shadows the old one."""
        old_gp = json.loads(_serialized(GeoPoint(lat=1.0, lon=2.0)))
        new_gp = json.loads(_serialized(GeoPoint(lat=3.0, lon=4.0)))
        save_node_result(f"{RID}/point", {"value": old_gp, "status": "succeeded"})
        save_node_result(f"{RID}/point", {"value": new_gp, "status": "succeeded"})
        loaded = latest_node_result(f"{RID}/point")
        assert loaded is not None
        reconstructed = _deserialize_value(json.dumps(loaded["value"]))
        assert reconstructed == GeoPoint(lat=3.0, lon=4.0)
