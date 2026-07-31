"""Tests that property_rids filters out non-property RIDs (like settings nodes)."""

from __future__ import annotations

import sqlite3

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
    def test_filters_out_non_numeric_rids(self):
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
