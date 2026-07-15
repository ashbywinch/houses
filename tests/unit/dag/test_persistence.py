"""Tests for dag persistence layer.

Uses SQLite in-memory database (no filesystem dependencies).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from dag.persistence import (
    _deserialize_value,
    _serialize_value,
    latest_node_result,
    save_node_result,
)

RID = "prop123"


class TestSerialisation:
    def test_string_roundtrip(self):
        s = _serialize_value("hello")
        assert _deserialize_value(s) == "hello"

    def test_string_json_literal_roundtrip(self):
        for raw in ("true", "false", "null", "42", "3.14"):
            s = _serialize_value(raw)
            assert _deserialize_value(s) == raw, f"'{raw}' roundtrip failed"

    def test_int_roundtrip(self):
        s = _serialize_value(42)
        assert _deserialize_value(s) == 42

    def test_float_roundtrip(self):
        s = _serialize_value(3.14)
        assert _deserialize_value(s) == 3.14

    def test_none_serialises_to_empty(self):
        s = _serialize_value(None)
        assert s == ""

    def test_complex_type_serialisation(self):
        @dataclass
        class Point:
            x: int
            y: int

        s = _serialize_value(Point(x=1, y=2))
        d = json.loads(s)
        assert d["x"] == 1
        assert d["y"] == 2

    def test_complex_type_deserialises(self):
        """Complex types serialised with _type/_module markers
        reconstruct as dicts (no automatic class reconstruction)."""
        s = '{"x": 3, "y": 4, "_type": "Point", "_module": "__main__"}'
        result = _deserialize_value(s)
        assert result["x"] == 3
        assert result["y"] == 4

    def test_empty_string_roundtrips(self):
        """Empty string should round-trip as empty string, not None."""
        assert _deserialize_value("") == ""

    def test_none_string_preserved_as_string(self):
        assert _deserialize_value("None") == "None"


class TestNodeResults:
    def test_save_and_load(self):
        save_node_result(f"{RID}/n1", {"status": "succeeded", "value": 42})
        loaded = latest_node_result(f"{RID}/n1")
        assert loaded is not None
        assert loaded["value"] == 42
        assert loaded["status"] == "succeeded"

    def test_nonexistent_returns_none(self):
        loaded = latest_node_result(f"{RID}/no_such_node")
        assert loaded is None

    def test_latest_by_node_id(self):
        save_node_result(f"{RID}/n2", {"status": "succeeded", "value": 1})
        save_node_result(f"{RID}/n2", {"status": "succeeded", "value": 2})
        loaded = latest_node_result(f"{RID}/n2")
        assert loaded is not None
        assert loaded["value"] == 2

    def test_node_result_includes_dep_timestamps(self):
        deps = {"dep_a": "2024-01-01T00:00:00", "dep_b": "2024-01-02T00:00:00"}
        save_node_result(f"{RID}/n3", {"status": "succeeded", "value": "v"}, deps)
        loaded = latest_node_result(f"{RID}/n3")
        assert loaded["_dep_timestamps"] == deps

    def test_persisted_at_timestamp(self):
        save_node_result(f"{RID}/n4", {"status": "succeeded", "value": "x"})
        loaded = latest_node_result(f"{RID}/n4")
        assert loaded["_persisted_at"] is not None
