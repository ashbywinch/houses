from __future__ import annotations

from datetime import datetime

import pytest

from houses.geo import GeoPoint
from houses.model import DerivedRow
from houses.model.persistence import (
    get_all_derived_values,
    get_all_source_values,
    get_all_user_inputs,
    get_current_user_input,
    get_latest_source_value,
    get_source_row_timestamp,
    get_user_row_timestamp,
    insert_source_value,
    insert_user_input,
    load_property_data,
    save_derived,
)


@pytest.fixture(autouse=True)
def _sqlite_memory():
    """Replace the global DB with a fresh in-memory database for each test."""
    import houses.model.persistence as per

    saved = per.get_db
    conn = _memory_db()
    per.get_db = lambda: conn
    yield
    per.get_db = saved


def _memory_db():
    import sqlite3

    import houses.model.persistence as per

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    per.get_db = lambda: conn
    per.init_db()
    return conn


RID = "prop123"


class TestSourceValues:
    def test_insert_and_latest(self):
        insert_source_value(RID, "rightmove_address", "10 High St", "Rightmove")
        row = get_latest_source_value(RID, "rightmove_address")
        assert row is not None
        assert row.value == "10 High St"
        assert row.source == "Rightmove"
        assert isinstance(row.row_id, int)

    def test_latest_by_created_at(self):
        insert_source_value(RID, "rightmove_address", "Old address", "Rightmove")
        insert_source_value(RID, "rightmove_address", "Mid address", "Rightmove")
        late = insert_source_value(RID, "rightmove_address", "New address", "Rightmove")
        row = get_latest_source_value(RID, "rightmove_address")
        assert row.value == "New address"
        assert row.row_id == late

    def test_get_all_source_values(self):
        insert_source_value(RID, "rightmove_address", "10 High St", "Rightmove")
        insert_source_value(RID, "rightmove_price", "250000", "Rightmove")
        all_sv = get_all_source_values(RID)
        assert set(all_sv.keys()) == {"rightmove_address", "rightmove_price"}

    def test_none_when_no_rows(self):
        row = get_latest_source_value("nonexistent", "rightmove_address")
        assert row is None

    def test_source_row_timestamp(self):
        ts = get_source_row_timestamp(RID, "rightmove_address")
        assert ts is None
        insert_source_value(RID, "rightmove_address", "10 High St", "Rightmove")
        ts = get_source_row_timestamp(RID, "rightmove_address")
        assert ts is not None
        assert isinstance(ts, datetime)


class TestUserInputs:
    def test_insert_and_current(self):
        insert_user_input(RID, "corrected_address", "10 High Street, London")
        row = get_current_user_input(RID, "corrected_address")
        assert row is not None
        assert row.value == "10 High Street, London"
        assert isinstance(row.row_id, int)

    def test_new_row_obsoletes_old(self):
        insert_user_input(RID, "corrected_address", "Old address")
        new_id = insert_user_input(RID, "corrected_address", "New address")
        row = get_current_user_input(RID, "corrected_address")
        assert row.value == "New address"
        assert row.row_id == new_id

    def test_precise_location(self):
        gp = GeoPoint(lat=51.5, lon=-0.1)
        insert_user_input(RID, "precise_location", gp)
        loc_row = get_current_user_input(RID, "precise_location")
        assert loc_row is not None
        assert loc_row.value == gp

    def test_get_all_user_inputs(self):
        insert_user_input(RID, "corrected_address", "10 High St")
        insert_user_input(RID, "precise_location", GeoPoint(lat=51.5, lon=-0.1))
        all_ui = get_all_user_inputs(RID)
        assert set(all_ui.keys()) == {"corrected_address", "precise_location"}

    def test_none_when_no_input(self):
        row = get_current_user_input(RID, "corrected_address")
        assert row is None

    def test_user_row_timestamp(self):
        ts = get_user_row_timestamp(RID, "corrected_address")
        assert ts is None
        insert_user_input(RID, "corrected_address", "Address")
        ts = get_user_row_timestamp(RID, "corrected_address")
        assert ts is not None


class TestDerivedValues:
    def test_save_and_load(self):
        dr = DerivedRow(
            value="10 High Street, London",
            dep_versions={"corrected_address": None, "rightmove_address": 3},
            source="user",
            error=None,
            updated_at=datetime(2025, 6, 1, 12, 0, 0),
        )
        save_derived(RID, "best_address", dr)
        loaded = get_all_derived_values(RID)
        assert "best_address" in loaded
        assert loaded["best_address"].value == "10 High Street, London"
        assert loaded["best_address"].dep_versions == {"corrected_address": None, "rightmove_address": 3}

    def test_replace_existing(self):
        dr1 = DerivedRow(
            value="Old",
            dep_versions={},
            source="test",
            error=None,
            updated_at=datetime(2025, 6, 1, 12, 0, 0),
        )
        save_derived(RID, "best_address", dr1)
        dr2 = DerivedRow(
            value="New",
            dep_versions={},
            source="test",
            error=None,
            updated_at=datetime(2025, 6, 2, 12, 0, 0),
        )
        save_derived(RID, "best_address", dr2)
        loaded = get_all_derived_values(RID)
        assert loaded["best_address"].value == "New"

    def test_empty_when_none(self):
        loaded = get_all_derived_values(RID)
        assert loaded == {}

    def test_load_property_data(self):
        insert_source_value(RID, "rightmove_address", "10 High St", "Rightmove")
        insert_user_input(RID, "corrected_address", "10 High Street, London")
        dr = DerivedRow(
            value="10 High Street, London",
            dep_versions={"corrected_address": 1, "rightmove_address": 1},
            source="user",
            error=None,
            updated_at=datetime(2025, 6, 1, 12, 0, 0),
        )
        save_derived(RID, "best_address", dr)
        pd = load_property_data(RID)
        assert pd.rid == RID
        assert "rightmove_address" in pd.sources
        assert "corrected_address" in pd.user_inputs
        assert "best_address" in pd.derived


class TestGeoPointPersistence:
    """GeoPoint serialization/deserialization through persistence."""

    def test_derived_geopoint_none_saves_and_loads_as_none(self):
        """Save None for a GeoPoint derived node → loads back as None, not 'None' string."""
        dr = DerivedRow(
            value=None,
            dep_versions={"precise_location": None, "best_address": None},
            source="",
            error=None,
            updated_at=datetime(2025, 6, 1, 12, 0, 0),
        )
        save_derived(RID, "best_location", dr)
        loaded = get_all_derived_values(RID)
        assert loaded["best_location"].value is None

    def test_derived_geopoint_valid_saves_and_loads_as_geopoint(self):
        gp = GeoPoint(lat=51.5, lon=-0.1)
        dr = DerivedRow(
            value=gp,
            dep_versions={},
            source="manual",
            error=None,
            updated_at=datetime(2025, 6, 1, 12, 0, 0),
        )
        save_derived(RID, "best_location", dr)
        loaded = get_all_derived_values(RID)
        assert loaded["best_location"].value == gp
        assert isinstance(loaded["best_location"].value, GeoPoint)

    def test_derived_geopoint_string_none_loads_as_string(self):
        """The literal string 'None' is preserved, not treated as Python None."""
        from houses.model.persistence import get_db

        conn = get_db()
        conn.execute(
            "INSERT INTO derived_values"
            " (property_id, node_id, value, dep_versions, source, error, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (RID, "best_location", "None", "{}", "", None, datetime(2025, 6, 1, 12, 0, 0).isoformat()),
        )
        conn.commit()
        loaded = get_all_derived_values(RID)
        assert loaded["best_location"].value == "None"
        assert isinstance(loaded["best_location"].value, str)

    def test_source_geopoint_stored_as_json_loads_as_geopoint(self):
        """Source values for GeoPoint nodes deserialize to GeoPoint on load."""
        gp = GeoPoint(lat=51.5, lon=-0.1)
        insert_source_value(RID, "rightmove_location", gp, "rightmove_map")
        loaded = get_all_source_values(RID)
        assert "rightmove_location" in loaded
        assert loaded["rightmove_location"].value == gp
        assert isinstance(loaded["rightmove_location"].value, GeoPoint)

    def test_source_geopoint_empty_string_loads_as_none(self):
        """Empty source value for GeoPoint node loads as None (no valid location)."""
        insert_source_value(RID, "rightmove_location", "", "rightmove_map")
        loaded = get_all_source_values(RID)
        assert loaded["rightmove_location"].value is None

    def test_derived_non_geopoint_none_loads_as_none(self):
        """Non-GeoPoint nodes with None value load as None (not empty string)."""
        dr = DerivedRow(
            value=None,
            dep_versions={},
            source="",
            error=None,
            updated_at=datetime(2025, 6, 1, 12, 0, 0),
        )
        save_derived(RID, "best_address", dr)
        loaded = get_all_derived_values(RID)
        assert loaded["best_address"].value is None
