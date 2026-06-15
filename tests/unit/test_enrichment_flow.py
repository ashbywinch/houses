from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import houses.model  # noqa: F401 — core types
import houses.model.property  # noqa: F401
import houses.model.rightmove  # noqa: F401
from houses.geo import GeoPoint
from houses.model.persistence import (
    get_latest_source_value,
    insert_source_value,
    insert_user_input,
    load_property_data,
)
from houses.model.property import best_location
from houses.model.resolver import resolve_property


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


RID = "test123"


class TestGeoCapture:
    async def test_best_location_uses_precise_when_set(self):
        gp = GeoPoint(lat=51.5, lon=-0.1)
        result, source = await best_location(
            precise_location=gp, rightmove_location=None, best_address="10 High St",
        )
        assert result == gp
        assert source == "User location"

    async def test_best_location_uses_rightmove_when_no_precise(self):
        rm = GeoPoint(lat=51.4, lon=-0.2)
        result, source = await best_location(
            precise_location=None, rightmove_location=rm, best_address="10 High St",
        )
        assert result == rm
        assert source == "Rightmove map"

    async def test_best_location_returns_none_when_no_inputs(self):
        result, source = await best_location(
            precise_location=None, rightmove_location=None, best_address=None,
        )
        assert result is None
        assert source == ""

    async def test_best_location_none_when_no_inputs(self):
        """No source data at all → best_location resolves to None (not 'None' string)."""
        results = await resolve_property(RID, node_ids=["best_location"])
        result = results.get("best_location")
        assert result is not None
        assert result.value is None
        assert result.source == ""

    async def test_best_location_none_survives_cache_reload(self):
        """None value gets saved + loaded as None, not string 'None'."""
        results = await resolve_property(RID, node_ids=["best_location"])
        assert results["best_location"].value is None
        results2 = await resolve_property(RID, node_ids=["best_location"])
        assert results2["best_location"].value is None
        assert not isinstance(results2["best_location"].value, str)

    async def test_best_location_via_resolver_with_precise(self):
        insert_user_input(RID, "precise_location", GeoPoint(51.5, -0.1))
        results = await resolve_property(RID, node_ids=["best_location"])
        assert results["best_location"].value == GeoPoint(51.5, -0.1)
        assert results["best_location"].source == "User location"

    async def test_best_location_via_resolver_with_rightmove(self):
        rm = GeoPoint(lat=52.0, lon=0.5)
        insert_source_value(RID, "rightmove_location", rm, "Rightmove map")
        results = await resolve_property(RID, node_ids=["best_location"])
        assert results["best_location"].value == rm
        assert results["best_location"].source == "Rightmove map"


class TestDualWrite:
    async def test_insert_source_values_then_resolve(self):
        insert_source_value(RID, "rightmove_address", "10 High St", "Rightmove")
        insert_source_value(RID, "rightmove_bedrooms", "3", "Rightmove")
        insert_source_value(RID, "rightmove_price", "250000", "Rightmove")

        results = await resolve_property(RID, node_ids=["best_address"])
        assert results["best_address"].value == "10 High St"
        assert results["best_address"].source == "Rightmove"

    async def test_user_input_with_dual_write(self):
        insert_source_value(RID, "rightmove_address", "RM St", "Rightmove")
        insert_user_input(RID, "corrected_address", "User Rd")
        results = await resolve_property(RID, node_ids=["best_address"])
        assert results["best_address"].value == "User Rd"
        assert results["best_address"].source == "User correction"

    async def test_derived_node_stored_after_resolve(self):
        insert_source_value(RID, "rightmove_address", "10 High St", "Rightmove")
        results = await resolve_property(RID, node_ids=["best_address"])
        assert results["best_address"].value == "10 High St"
        data = load_property_data(RID)
        assert "best_address" in data.derived
        assert data.derived["best_address"].value == "10 High St"

    async def test_re_enrichment_inserts_new_source_rows(self):
        insert_source_value(RID, "rightmove_address", "Old address", "Rightmove")
        insert_source_value(RID, "rightmove_address", "New address", "Rightmove")
        latest = get_latest_source_value(RID, "rightmove_address")
        assert latest.value == "New address"


class TestWebEndpoint:
    @pytest.fixture(autouse=True)
    def _seed_data(self):
        insert_source_value(RID, "rightmove_address", "10 High St", "Rightmove")
        insert_source_value(RID, "rightmove_bedrooms", "3", "Rightmove")
        insert_source_value(RID, "rightmove_price", "250000", "Rightmove")
        insert_user_input(RID, "precise_location", GeoPoint(51.5, -0.1))

    def test_html_detail_page_renders(self):
        from houses.server import app

        client = TestClient(app)
        resp = client.get(f"/properties/{RID}", headers={"accept": "text/html"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "10 High St" in resp.text
        assert "3" in resp.text
        assert "250,000" in resp.text

    def test_html_renders_without_location_data(self):
        """Page renders without error when only basic address data exists."""
        from houses.server import app

        client = TestClient(app)
        resp = client.get(f"/properties/{RID}", headers={"accept": "text/html"})
        assert resp.status_code == 200
        assert "10 High St" in resp.text
        assert "Location" in resp.text

    def test_html_404_for_unknown_rid(self):
        from houses.server import app

        client = TestClient(app)
        resp = client.get("/properties/unknown123", headers={"accept": "text/html"})
        assert resp.status_code == 404

    def test_html_includes_map_url_when_coords_present(self):
        insert_source_value(RID, "rightmove_location", GeoPoint(51.5, -0.1), "Rightmove map")
        from houses.server import app

        client = TestClient(app)
        resp = client.get(f"/properties/{RID}", headers={"accept": "text/html"})
        assert resp.status_code == 200
        assert "google.com/maps" in resp.text

    def test_enhance_endpoint_returns_html(self):
        from houses.server import app

        client = TestClient(app)
        resp = client.post(
            f"/properties/{RID}/enhance",
            data={"action": "address", "corrected_address": "User Rd"},
            headers={"accept": "text/html"},
        )
        assert resp.status_code == 200
        assert "User Rd" in resp.text

    def test_staleness_endpoint_returns_html(self):
        from houses.server import app

        client = TestClient(app)
        resp = client.get(f"/properties/{RID}/staleness?nodes=best_location")
        assert resp.status_code == 200
        assert "stale-spinner" in resp.text or "stale-spinner--fresh" in resp.text

    def test_map_picker_endpoint_returns_html(self):
        from houses.server import app

        client = TestClient(app)
        resp = client.get(f"/properties/{RID}/map-picker", headers={"accept": "text/html"})
        assert resp.status_code == 200
        assert "leaflet-map" in resp.text


class TestSheetImport:
    """Tests for the first-view import-from-sheet flow."""

    def test_import_noop_when_sheet_not_configured(self):
        """When no sheet_id is set, import returns (False, []) gracefully."""
        import asyncio

        from houses.web.router import _try_import_from_sheet

        imported, warnings = asyncio.run(_try_import_from_sheet("any_rid"))
        assert imported is False
        assert warnings == []

    async def test_imported_property_resolves_after_seed(self):
        """Simulate the import by seeding source values directly."""
        insert_source_value(RID, "rightmove_address", "Imported Address", "Rightmove")
        insert_source_value(RID, "rightmove_price", "300000", "Rightmove")
        results = await resolve_property(RID, node_ids=["best_address"])
        assert results["best_address"].value == "Imported Address"

    async def test_import_upgrades_address_with_postcode(self):
        """When the sheet has both address and postcode, best_address includes the full postcode."""
        insert_source_value(RID, "rightmove_address", "Pembroke Avenue, Hersham, KT12", "Rightmove")
        insert_user_input(RID, "corrected_address", "Pembroke Avenue, Hersham, KT12 4NT")
        results = await resolve_property(RID, node_ids=["best_address"])
        assert results["best_address"].value == "Pembroke Avenue, Hersham, KT12 4NT"
        assert results["best_address"].source == "User correction"

    async def test_import_without_lat_lng_produces_no_location(self):
        """When the sheet has no lat/lng, best_location resolves to None."""
        insert_source_value(RID, "rightmove_address", "10 High St", "Rightmove")
        results = await resolve_property(RID, node_ids=["best_location"])
        assert results["best_location"].value is None

    async def test_import_with_postcode_but_no_lat_lng(self):
        """Realistic simulation: address + postcode imported, no lat/lng in sheet.
        best_address uses the corrected address, best_location is None."""
        insert_source_value(RID, "rightmove_address", "Pembroke Avenue, Hersham, KT12", "Rightmove")
        insert_user_input(RID, "corrected_address", "Pembroke Avenue, Hersham, KT12 4NT")
        results = await resolve_property(RID, node_ids=["best_address", "best_location"])
        assert results["best_address"].value == "Pembroke Avenue, Hersham, KT12 4NT"
        assert results["best_address"].source == "User correction"
        assert results["best_location"].value is None

    async def test_import_with_approx_lat_lng(self):
        """When the sheet has Approx Latitude/Longitude, these are imported as rightmove_location
        and best_location uses them (no geocoding needed)."""
        gp = GeoPoint(lat=51.37, lon=-0.4)
        insert_source_value(RID, "rightmove_address", "Some Road, Hersham", "Rightmove")
        insert_source_value(RID, "rightmove_location", gp, "Rightmove map")
        results = await resolve_property(RID, node_ids=["best_location"])
        assert results["best_location"].value == gp
        assert results["best_location"].source == "Rightmove map"

    async def test_import_with_actual_lat_lng_overrides_approx(self):
        """When both approx and actual lat/lng exist, actual (precise_location) takes priority."""
        approx = GeoPoint(lat=51.37, lon=-0.4)
        actual = GeoPoint(lat=51.38, lon=-0.41)
        insert_source_value(RID, "rightmove_address", "Some Road, Hersham", "Rightmove")
        insert_source_value(RID, "rightmove_location", approx, "Rightmove map")
        insert_user_input(RID, "precise_location", actual)
        results = await resolve_property(RID, node_ids=["best_location"])
        assert results["best_location"].value == actual
        assert results["best_location"].source == "User location"

    async def test_best_location_uses_rightmove_when_no_precise(self):
        """When rightmove_location exists but not precise_location, rightmove is used."""
        rm = GeoPoint(lat=51.37, lon=-0.4)
        insert_source_value(RID, "rightmove_address", "Some Road, Hersham", "Rightmove")
        insert_source_value(RID, "rightmove_location", rm, "Rightmove map")
        results = await resolve_property(RID, node_ids=["best_location"])
        assert results["best_location"].value == rm
        assert results["best_location"].source == "Rightmove map"
