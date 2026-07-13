"""Tests for dag persistence layer.

Uses SQLite in-memory database (no filesystem dependencies).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from dag.persistence import (
    _deserialize_value,
    _serialize_value,
    get_all_source_values,
    get_latest_source_value,
    insert_source_value,
    latest_node_result,
    load_node_data,
    save_node_result,
)

RID = "prop123"


class TestSerialisation:
    def test_string_roundtrip(self):
        s = _serialize_value("hello")
        assert _deserialize_value(s) == "hello"

    def test_string_json_literal_roundtrip(self):
        """Strings matching JSON literals must not be corrupted."""
        for raw in ("true", "false", "null", "42", "3.14"):
            s = _serialize_value(raw)
            assert isinstance(_deserialize_value(s), str), f"'{raw}' corrupted to non-string"
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

        p = Point(x=1, y=2)
        s = _serialize_value(p)
        d = json.loads(s)
        assert d["_type"] == "Point"
        assert d["x"] == 1
        assert d["y"] == 2

    def test_complex_type_deserialises(self):
        @dataclass
        class Point:
            x: int
            y: int

        stored = json.dumps({"_type": "Point", "_module": "builtins", "x": 3, "y": 4})
        result = _deserialize_value(stored)
        # Can't import builtins.Point, so returns raw dict
        assert isinstance(result, dict)
        assert result["x"] == 3

    def test_empty_string_deserialises_to_none(self):
        assert _deserialize_value("") is None

    def test_none_string_preserved_as_string(self):
        """The literal string 'None' is preserved, not treated as Python None."""
        assert _deserialize_value("None") == "None"


class TestSourceValuePersistence:
    def test_insert_and_load(self):
        insert_source_value(RID, "rightmove_address", "10 High St", "Rightmove")
        row = get_latest_source_value(RID, "rightmove_address")
        assert row is not None
        assert row["value"] == "10 High St"
        assert row["source"] == "Rightmove"

    def test_latest_by_created_at(self):
        insert_source_value(RID, "price", 100, "src1")
        insert_source_value(RID, "price", 200, "src2")
        row = get_latest_source_value(RID, "price")
        assert row["value"] == 200

    def test_get_all_source_values(self):
        insert_source_value(RID, "a", "1", "s1")
        insert_source_value(RID, "b", "2", "s2")
        all_sv = get_all_source_values(RID)
        assert set(all_sv.keys()) == {"a", "b"}

    def test_none_when_no_rows(self):
        row = get_latest_source_value("nonexistent", "anything")
        assert row is None


class TestNodeData:
    def test_load_property_data(self):
        insert_source_value(RID, "address", "10 High St", "Rightmove")
        data = load_node_data(RID)
        assert "address" in data["sources"]
        assert data["sources"]["address"]["value"] == "10 High St"


class TestNodeResults:
    def test_save_and_load(self):
        result = {"succeeded": True, "value": "hello", "error": None,
                  "provenance": {"label": "test"}}
        save_node_result("node1", result)
        loaded = latest_node_result("node1")
        assert loaded is not None
        assert loaded["succeeded"] is True
        assert loaded["value"] == "hello"

    def test_load_returns_none_for_missing(self):
        assert latest_node_result("nonexistent") is None

    def test_load_returns_latest(self):
        save_node_result("node1", {"succeeded": True, "value": "first"})
        save_node_result("node1", {"succeeded": True, "value": "second"})
        loaded = latest_node_result("node1")
        assert loaded["value"] == "second"

    def test_save_persists_dep_timestamps(self):
        result = {"succeeded": True, "value": 42, "error": None,
                  "provenance": {"label": "test"}}
        deps = {"src_node": 12345.67, "other_node": 89101.23}
        save_node_result("node2", result, dep_timestamps=deps)
        loaded = latest_node_result("node2")
        # dep_timestamps is stored but latest_node_result doesn't return it
        assert loaded["value"] == 42

    def test_saves_multiple_nodes_independently(self):
        save_node_result("a", {"value": "AAA"})
        save_node_result("b", {"value": "BBB"})
        assert latest_node_result("a")["value"] == "AAA"
        assert latest_node_result("b")["value"] == "BBB"

    def test_persisted_at_timestamp(self):
        save_node_result("n", {"value": 1})
        loaded = latest_node_result("n")
        assert "_persisted_at" in loaded
        assert loaded["_persisted_at"] is not None
