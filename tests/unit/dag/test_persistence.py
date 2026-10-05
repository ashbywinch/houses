"""Tests for dag persistence layer.

Uses SQLite in-memory database (no filesystem dependencies).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from dag import persistence as per
from dag.persistence import (
    _deserialize_value,
    _serialize_value,
    latest_node_result,
    property_created_at,
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

    def test_none_serialises_to_none(self):
        assert _serialize_value(None) is None

    def test_complex_type_serialisation(self):
        @dataclass
        class Point:
            x: int
            y: int

        s = _serialize_value(Point(x=1, y=2))
        assert s is not None
        d = json.loads(s)
        assert d["x"] == 1
        assert d["y"] == 2

    def test_complex_type_deserialises_fails_fast(self):
        """Complex types with unresolvable _type/_module raise instead of silent fallback."""
        import pytest

        s = '{"x": 3, "y": 4, "_type": "Point", "_module": "__main__"}'
        with pytest.raises(AttributeError):
            _deserialize_value(s)

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
        assert loaded is not None
        assert loaded["_dep_timestamps"] == deps

    def test_persisted_at_timestamp(self):
        save_node_result(f"{RID}/n4", {"status": "succeeded", "value": "x"})
        loaded = latest_node_result(f"{RID}/n4")
        assert loaded is not None
        assert loaded["_persisted_at"] is not None



class TestStorageColumns:
    """Each part of a record has its own column; ONLY provenance is compressed.

    2026-10-05: the whole record used to sit in one zlib'd `result_json`
    column, so every read — attempt load, staleness, the listing build —
    inflated a payload that is mostly provenance.
    """

    def _row(self, node_id: str):
        return per._get_db().execute(
            "SELECT status, value_json, error, error_detail_json, source_url, provenance_z, extra_json,"
            " source_label FROM node_results WHERE node_id=?",
            (node_id,),
        ).fetchone()

    def test_fields_land_in_their_own_columns(self):
        record = {
            "status": "impossible",
            "error": "no route",
            "error_detail": {"code": "no_route", "message": "no route"},
            "source_url": "https://example.test/x",
            "provenance": {"label": "P", "tree": {"deep": ["z" * 400]}},
        }
        save_node_result(f"{RID}/columns", record)
        row = self._row(f"{RID}/columns")

        assert row["status"] == "impossible"
        assert row["error"] == "no route"
        assert json.loads(row["error_detail_json"]) == {"code": "no_route", "message": "no route"}
        assert row["source_url"] == "https://example.test/x"
        assert row["value_json"] is None

        blob = row["provenance_z"]
        assert isinstance(blob, bytes) and blob[:1] == b"\x78", "provenance stays zlib"
        assert per.decompress_result(blob).startswith('{"label": "P"'), "and is the tree"

    def test_values_are_plain_text_and_provenance_is_not_in_them(self):
        save_node_result(
            f"{RID}/plain",
            {"status": "succeeded", "value": {"m": "VALUE_MARKER"}, "provenance": {"label": "PROV_MARKER"}},
        )
        row = self._row(f"{RID}/plain")

        assert json.loads(row["value_json"]) == {"m": "VALUE_MARKER"}
        assert "PROV_MARKER" not in str(row["value_json"])

    def test_a_record_read_never_carries_the_provenance_tree(self):
        tree = {"label": "P", "tree": {"deep": ["z" * 400]}}
        save_node_result(f"{RID}/lazy", {"status": "succeeded", "value": 1, "provenance": tree})

        loaded = latest_node_result(f"{RID}/lazy")
        assert loaded is not None
        assert "provenance" not in loaded, "the tree is an explicit ask"
        assert per.latest_node_provenance(f"{RID}/lazy") == tree

    def test_a_legacy_whole_row_zlib_blob_still_reads(self):
        """Rows written before the split carry everything in one zlib blob —
        the reader accepts that shape until the migration converts them."""
        legacy = {"status": "succeeded", "value": "legacy", "provenance": {"label": "old"}}
        conn = per._get_db()
        conn.execute("DROP TABLE node_results")
        conn.execute(
            "CREATE TABLE node_results (id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL,"
            " result_json TEXT NOT NULL, dep_timestamps TEXT, created_at TEXT NOT NULL, code_version TEXT)"
        )
        conn.execute(
            "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
            (
                f"{RID}/legacy",
                per.compress_result(json.dumps(legacy)),
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        per._reset_caches()

        loaded = latest_node_result(f"{RID}/legacy")
        assert loaded is not None
        assert loaded["value"] == "legacy"
        assert per.latest_node_provenance(f"{RID}/legacy") == {"label": "old"}
class TestPropertyCreatedAt:
    def test_returns_none_for_unknown_property(self):
        assert property_created_at("nonexistent") is None

    def test_returns_iso_timestamp_for_existing_property(self):
        save_node_result("prop123/rightmove_url", {"status": "succeeded", "value": "https://..."})
        ts = property_created_at("prop123")
        assert ts is not None
        # Must be ISO-8601 format
        assert "T" in ts
        assert ts.endswith("Z") or "+" in ts or ts.endswith("00:00")

    def test_uses_earliest_result(self):
        save_node_result("prop456/rightmove_url", {"status": "succeeded", "value": "url1"})
        import time

        time.sleep(0.01)  # ensure different timestamp
        save_node_result("prop456/rightmove_url", {"status": "succeeded", "value": "url2"})
        ts = property_created_at("prop456")
        assert ts is not None
        # The earliest (first) timestamp should be before or equal to the latest
        latest = latest_node_result("prop456/rightmove_url")
        assert latest is not None
        assert ts <= latest["_persisted_at"]


def test_init_db_creates_latest_row_index():
    """A fresh database must get the node_results latest-row index — the
    init_db rewrite dropped it, so fresh installs lost the fast
    latest_node_result path (the live DB keeps the old index, but a new
    deploy would not)."""
    from dag.persistence import _get_db, init_db

    init_db()
    rows = _get_db().execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_nr_node'").fetchall()
    assert rows, "idx_nr_node must exist on a fresh database"


class TestProcessorPipeline:
    """Pipeline contract: one node's failure never kills the drain, and
    later items still process — the regression the pre-processor save
    worker had (one bad payload killed persistence permanently; see
    .kilo/plans/dag-save-queue.md).
    """

    def test_background_loop_survives_node_failure(self, caplog):
        import asyncio
        import contextlib
        import logging

        import dag.scheduler as sched_mod

        sched = sched_mod.AsyncQueueScheduler(respect_time=False)
        done: list[str] = []

        class _Flaky:
            _id = "flaky/a"
            _retry_at = None

            async def refresh(self, force: bool = False) -> None:
                raise RuntimeError("boom")

        class _Fine:
            _id = "flaky/b"
            _retry_at = None

            async def refresh(self, force: bool = False) -> None:
                done.append(self._id)

        sched.schedule(_Flaky())  # type: ignore[arg-type]  # why: scheduler.schedule takes DerivedNode; the test class is structurally one
        sched.schedule(_Fine())  # type: ignore[arg-type]  # why: same structural-subclass rationale as _Flaky above

        async def _run() -> None:
            task = asyncio.create_task(sched._background_loop())
            for _ in range(100):
                if done:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        loop = asyncio.new_event_loop()
        try:
            with caplog.at_level(logging.ERROR):
                loop.run_until_complete(_run())
        finally:
            loop.close()

        assert done == ["flaky/b"], "processing continues after an error"
        assert any("flaky/a" in r.getMessage() for r in caplog.records), "failure must be logged"
