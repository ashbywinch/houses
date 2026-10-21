"""Persistence round-trips: what a node result stores is what a reader gets.

The value is stored as plain JSON (``DagJSONEncoder`` for the types it owns) —
the old ``_type``/``_module`` marker encoding is gone, so these tests assert the
stored dict, not a decoded object.
"""

from __future__ import annotations

import json

from dag.persistence import latest_node_result, save_node_result

RID = "prop123"

# A GeoPoint as a node serialises it (``dump_python(mode="json")``).
GEOPOINT_WIRE = {"lat": 51.5, "lon": -0.1}


class TestValuePersistence:
    """Full SQLite round-trip for a node's value via save/latest_node_result."""

    def test_none_value_saves_and_loads_as_none(self):
        save_node_result(f"{RID}/best_location", {"value": None, "status": "succeeded"})
        loaded = latest_node_result(f"{RID}/best_location")
        assert loaded is not None
        assert loaded["value"] is None

    def test_geopoint_wire_roundtrips(self):
        save_node_result(
            f"{RID}/best_location",
            {"value": GEOPOINT_WIRE, "status": "succeeded", "source": "manual"},
        )
        loaded = latest_node_result(f"{RID}/best_location")
        assert loaded is not None
        assert loaded["value"] == GEOPOINT_WIRE

    def test_geopoint_from_source_node_roundtrips(self):
        """GeoPoint stored under a ``rightmove_location`` node id loads back."""
        save_node_result(
            f"{RID}/rightmove_location",
            {"value": GEOPOINT_WIRE, "status": "succeeded", "source": "rightmove_map"},
        )
        loaded = latest_node_result(f"{RID}/rightmove_location")
        assert loaded is not None
        assert loaded["value"] == GEOPOINT_WIRE

    def test_inline_none_string_survives(self):
        """When ``"value": "None"`` is stored in the result JSON directly,
        it comes back as the Python string ``"None"``, not Python None."""
        from datetime import UTC, datetime

        save_node_result(
            f"{RID}/best_location",
            {"value": "None", "status": "succeeded"},
            created_at=datetime.now(UTC).isoformat(),
        )
        loaded = latest_node_result(f"{RID}/best_location")
        assert loaded is not None
        assert loaded["value"] == "None"
        assert isinstance(loaded["value"], str)

    def test_empty_string_survives(self):
        """An empty string is a value, not an absent one."""
        save_node_result(f"{RID}/notes", {"value": "", "status": "succeeded"})
        loaded = latest_node_result(f"{RID}/notes")
        assert loaded is not None
        assert loaded["value"] == ""

    def test_non_geopoint_none_loads_as_none(self):
        """A plain string node with ``value: None`` loads back as None."""
        save_node_result(f"{RID}/best_address", {"value": None, "status": "succeeded"})
        loaded = latest_node_result(f"{RID}/best_address")
        assert loaded is not None
        assert loaded["value"] is None

    def test_two_nodes_are_independent(self):
        """Multiple distinct node ids storing values don't interfere."""
        save_node_result(f"{RID}/first", {"value": {"lat": 51.5, "lon": -0.1}, "status": "succeeded"})
        save_node_result(f"{RID}/second", {"value": {"lat": 52.0, "lon": 0.0}, "status": "succeeded"})
        first = latest_node_result(f"{RID}/first")
        second = latest_node_result(f"{RID}/second")
        assert first is not None and second is not None
        assert first["value"] == {"lat": 51.5, "lon": -0.1}
        assert second["value"] == {"lat": 52.0, "lon": 0.0}

    def test_latest_replaces_older(self):
        """Saving a new node result for the same id shadows the old one."""
        save_node_result(f"{RID}/point", {"value": {"lat": 1.0, "lon": 2.0}, "status": "succeeded"})
        save_node_result(f"{RID}/point", {"value": {"lat": 3.0, "lon": 4.0}, "status": "succeeded"})
        loaded = latest_node_result(f"{RID}/point")
        assert loaded is not None
        assert loaded["value"] == {"lat": 3.0, "lon": 4.0}


class TestCompressedStorage:
    """Each field has its own column; only provenance is compressed.

    2026-10-05: the whole record used to be zlib'd in one column, so every
    node read inflated a payload that is mostly provenance (the listing build
    inflated ~1000 of them per request).  Values are plain text; the
    provenance trees keep their ~25x in their own blob column, read only when
    a consumer asks for them.
    """

    def test_only_provenance_is_compressed_and_roundtrips(self):
        from dag import persistence as p

        big = {
            "status": "succeeded",
            "value": {"x": "y" * 500},
            "provenance": {"label": "L", "sources": {"a": {"value": "z" * 500}}},
        }
        node_id = f"{RID}/compressed"
        save_node_result(node_id, big)
        row = p._get_db().execute(
            "SELECT value_json, provenance_z FROM node_results WHERE node_id=?", (node_id,)
        ).fetchone()

        assert "y" * 500 in row["value_json"], "the value is readable without inflation"
        blob = row["provenance_z"]
        assert isinstance(blob, bytes), f"provenance is a zlib BLOB, got {type(blob).__name__}"
        assert blob[:1] == b"\x78" and "z" * 500 not in blob.decode("latin-1"), (
            "the provenance subtree is the compressed part"
        )
        assert len(blob) < len(json.dumps(big["provenance"])) // 2, "compression stays meaningful"

        loaded = latest_node_result(node_id)
        assert loaded is not None
        assert loaded["status"] == "succeeded"
        assert loaded["value"] == big["value"]
        assert "provenance" not in loaded, "reading a record must not inflate the tree"
        assert p.latest_node_provenance(node_id) == big["provenance"]

    def test_legacy_rows_of_both_old_shapes_still_load(self):
        """A pre-split database reads unchanged: both the plain-JSON rows and
        the whole-row zlib blobs — the migration converts them, and until then
        every reader must cope."""
        from dag import persistence as p

        node_id = f"{RID}/legacy"
        conn = p._get_db()
        conn.execute("DROP TABLE node_results")
        conn.execute(
            "CREATE TABLE node_results (id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL,"
            " result_json TEXT NOT NULL, dep_timestamps TEXT, created_at TEXT NOT NULL, code_version TEXT)"
        )
        conn.execute(
            "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
            (node_id, json.dumps({"status": "succeeded", "value": {"legacy": True}}), "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
            (
                f"{node_id}_z",
                p.compress_result(json.dumps({"status": "succeeded", "value": {"zlibbed": True}})),
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        p._reset_caches()

        loaded = latest_node_result(node_id)
        assert loaded is not None
        assert loaded["value"] == {"legacy": True}
        zlibbed = latest_node_result(f"{node_id}_z")
        assert zlibbed is not None
        assert zlibbed["value"] == {"zlibbed": True}


class TestDeleteNodeResultsForRid:
    """The remove-property delete must not honor LIKE wildcards in the
    user-supplied rid — a '%' or '_' would wipe unrelated properties'
    rows (PR #68 review, High)."""

    @staticmethod
    def _seed(rid: str) -> None:
        save_node_result(f"{rid}/node", {"status": "succeeded", "value": 1})

    def test_wildcard_rid_does_not_delete_other_properties(self):
        from dag import persistence as p

        self._seed("123")
        self._seed("456")
        p.delete_node_results_for_rid("%")
        assert p.latest_node_result("123/node") is not None
        assert p.latest_node_result("456/node") is not None

    def test_underscore_in_rid_matches_only_the_literal(self):
        from dag import persistence as p

        self._seed("12_3")
        self._seed("12X3")
        p.delete_node_results_for_rid("12_3")
        assert p.latest_node_result("12_3/node") is None
        assert p.latest_node_result("12X3/node") is not None, (
            "an unescaped '_' matches any character — '12_3' would delete '12X3'"
        )
