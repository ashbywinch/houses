"""property_rids() separates properties from the global settings namespace.

It must NOT filter anything else: a prefix that is not a valid property RID is
test-data pollution, and the startup loader has a guard that exists to fail
loudly on it.  Filtering non-numeric prefixes here made that guard unreachable,
so a stray `test_shape/…` row was silently ignored (found 2026-09-10 while
investigating a reported duplicate property)."""

from __future__ import annotations

import sqlite3

import pytest

import dag.persistence as per


def _seed_test_data(conn):
    """Insert node results with property RIDs and a settings RID."""
    conn.execute(
        "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
        ("89306649/rightmove_price", '{"status":"succeeded","value":"GBP 500000"}', "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
        ("89306649/postcode", '{"status":"succeeded"}', "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
        ("settings/mortgage_rate", '{"status":"succeeded"}', "2026-01-01T00:00:00"),
    )
    conn.commit()


class TestPropertyRids:
    def test_keeps_the_settings_namespace_out(self):
        """property_rids must exclude RIDs like 'settings' that aren't numeric property IDs."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        saved = per._get_db
        per._get_db = lambda: conn
        per.init_db()
        _seed_test_data(conn)

        rids = per.property_rids()

        per._get_db = saved
        conn.close()

        assert "89306649" in rids, "Numeric property RID should be included"
        assert "settings" not in rids, "Non-numeric RID 'settings' should be excluded"
        assert len(rids) == 1, f"Expected 1 property RID, got {len(rids)}: {rids}"


class TestPollutionIsVisibleToTheLoader:
    def test_a_stray_prefix_is_not_filtered_out(self):
        """The loader's guard can only fire for prefixes this function returns."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        saved = per._get_db
        per._get_db = lambda: conn
        per.init_db()
        conn.execute(
            "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
            ("89306649/rightmove_price", '{"status":"succeeded","value":"GBP 500000"}', "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
            ("test_shape/rightmove_price", '{"status":"succeeded"}', "2026-01-01T00:00:00"),
        )
        conn.commit()

        try:
            rids = per.property_rids()
        finally:
            per._get_db = saved
            conn.close()

        assert "89306649" in rids
        assert "test_shape" in rids, (
            "a test-data prefix was filtered out here, so the loader's loud "
            "pollution guard can never see it"
        )

    def test_the_loader_fails_loudly_on_a_test_data_row(self):
        """Startup must refuse to serve a DB holding rows under a non-property id."""
        from houses.nodes.bootstrap import load_property_nodes_from_db

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        saved = per._get_db
        per._get_db = lambda: conn
        per.init_db()
        conn.execute(
            "INSERT INTO node_results (node_id, result_json, created_at) VALUES (?, ?, ?)",
            ("test_shape/postcode", '{"status":"succeeded"}', "2026-01-01T00:00:00"),
        )
        conn.commit()

        try:
            with pytest.raises(RuntimeError, match="test-data"):
                load_property_nodes_from_db()
        finally:
            per._get_db = saved
            conn.close()
