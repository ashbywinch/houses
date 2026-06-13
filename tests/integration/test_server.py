"""Tests for the FastAPI server endpoints."""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from houses.commute import Commute, CommuteBreakdown
from houses.config import settings
from houses.enrichment_runner import header_to_enrichment_field, run_backfill_enrichment
from houses.property import CouncilTaxInfo, EnrichedProperty
from houses.server import app
from houses.services import Services
from houses.sheets import COLUMN_HEADERS, VIEW_HEADERS
from tests.helpers import FakeCommuteRouter, FakeEPC

client = TestClient(app)


class TestInjectProperty:
    VALID_PAYLOAD = {
        "url": "https://www.rightmove.co.uk/properties/123456789",
        "address": "High Street, Some Town, RG14 1AA",
    }

    MAIDENHEAD_PAYLOAD = {
        "url": "https://www.rightmove.co.uk/properties/999999991",
        "address": "Shoppenhangers Road, Maidenhead, SL6",
        "bedrooms": 5,
        "price": 775000,
    }

    @pytest.mark.integration
    def test_valid_payload_returns_data(self):
        resp = client.post("/properties", json=self.VALID_PAYLOAD)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "data" in body

    @pytest.mark.integration
    def test_minimal_payload_with_only_url(self):
        original = settings.rightmove_sample_page
        fixture_dir = Path(__file__).parent.parent / "fixtures"
        settings.rightmove_sample_page = str(fixture_dir / "rightmove_sample.html")
        try:
            resp = client.post("/properties", json={"url": "https://www.rightmove.co.uk/properties/1"})
            assert resp.status_code == 200
            assert "url" in resp.json()["data"]
        finally:
            settings.rightmove_sample_page = original

    @pytest.mark.integration
    def test_accepts_any_url(self):
        payload = {**self.VALID_PAYLOAD, "url": "https://example.com/"}
        resp = client.post("/properties", json=payload)
        assert resp.status_code == 200

    def test_rejects_existing_property_without_fields(self):
        """Re-enriching an existing property must specify which fields to update."""
        from houses.config import settings
        from houses.sheets import col_index

        rid_index = col_index("Rightmove ID")

        # Build a fake row that looks like the sheet's row 2
        fake_row = [""] * 38
        fake_row[rid_index] = "88375569"

        # Mock get_client to return a sheet with this row
        fake_cell_data = [[f"header {i}" for i in range(38)]] + [fake_row]
        mock_ws = MagicMock()
        mock_ws.get_all_values.return_value = fake_cell_data

        mock_sh = MagicMock()
        mock_sh.worksheet.return_value = mock_ws

        mock_client = MagicMock()
        mock_client.open_by_key.return_value = mock_sh

        original_sheet_id = settings.sheet_id
        settings.sheet_id = "fake-sheet-id-for-test"
        try:
            with patch("houses.server.get_client", return_value=mock_client):
                resp = client.post(
                    "/properties",
                    json={"url": "https://www.rightmove.co.uk/properties/88375569"},
                )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text[:100]}"
            body = resp.json()
            assert "already exists" in body.get("error", ""), f"Missing 'already exists' message: {body}"
            assert "fields=" in body.get("error", ""), f"Missing fields= hint: {body}"
        finally:
            settings.sheet_id = original_sheet_id

    @pytest.mark.integration
    def test_enrichment_fields_present(self):
        resp = client.post("/properties", json=self.VALID_PAYLOAD)
        data = resp.json()["data"]
        assert "simon_commute" in data
        assert "lorena_commute" in data
        assert "petrol" in data
        assert "primary_school" in data
        assert "secondary_school" in data
        assert "town_description" in data
        assert "commute_breakdown" in data
        assert "epc_rating" in data

    @pytest.mark.integration
    def test_maidenhead_outcode_gets_full_enrichment(self):
        """Address with only outcode 'SL6' — server must use full street
        address for geocoding so transit/petrol/schools all return results."""
        resp = client.post("/properties", json=self.MAIDENHEAD_PAYLOAD)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["url"] == self.MAIDENHEAD_PAYLOAD["url"]
        assert data["address"] == self.MAIDENHEAD_PAYLOAD["address"]
        assert data["postcode"] == "SL6"
        simon = data.get("simon_commute") or {}
        assert simon.get("duration_minutes") is not None, f"Simon missing: {simon}"
        lorena = data.get("lorena_commute") or {}
        assert lorena.get("duration_minutes") is not None, f"Lorena missing: {lorena}"
        petrol = data.get("petrol") or {}
        assert petrol.get("daily_cost_gbp") is not None, f"Petrol missing: {petrol}"
        assert data.get("primary_school") is not None, "No primary school"
        assert data.get("secondary_school") is not None, "No secondary school"


class TestBackfillView:
    """Tests for POST /properties."""

    @staticmethod
    def _parse_rows(resp) -> list[dict]:
        """Parse NDJSON streaming response and return the final status rows."""
        rows = []
        for line in resp.text.strip().split("\n"):
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("results") is not None:
                return obj["results"]
            if obj.get("type") == "row" and obj.get("status") != "enriching":
                rows.append(obj)
        return rows

    @staticmethod
    def _parse_start(resp) -> dict:
        """Parse NDJSON streaming response and return the first ('start') line."""
        first = resp.text.strip().split("\n")[0]
        return json.loads(first) if first else {}

    def test_column_headers_match_sheets(self):
        """Test DATA_HEADERS and VIEW_HEADERS match canonical sources.
        If you add a column to COLUMN_HEADERS or VIEW_HEADERS in sheets.py,
        this test will fail and remind you to update the test data below."""
        assert self.DATA_HEADERS == COLUMN_HEADERS, (
            f"DATA_HEADERS mismatch with COLUMN_HEADERS.\n"
            f"DATA_HEADERS has {len(self.DATA_HEADERS)} items, "
            f"COLUMN_HEADERS has {len(COLUMN_HEADERS)} items.\n"
            f"Diff (+ in COLUMN_HEADERS, - in DATA_HEADERS):\n"
            f"  +{set(COLUMN_HEADERS) - set(self.DATA_HEADERS)}\n"
            f"  -{set(self.DATA_HEADERS) - set(COLUMN_HEADERS)}"
        )
        assert self.VIEW_HEADERS == VIEW_HEADERS, (
            f"VIEW_HEADERS mismatch with VIEW_HEADERS.\n"
            f"VIEW_HEADERS has {len(self.VIEW_HEADERS)} items, "
            f"VIEW_HEADERS has {len(VIEW_HEADERS)} items.\n"
            f"Diff (+ in VIEW_HEADERS, - in test):\n"
            f"  +{set(VIEW_HEADERS) - set(self.VIEW_HEADERS)}\n"
            f"  -{set(self.VIEW_HEADERS) - set(VIEW_HEADERS)}"
        )

    VIEW_HEADERS = [
        "Listing Address",
        "Rightmove Link",
        "Map",
        "Rightmove ID",
        "Purchase Cost (£)",
        "EPC Rating",
        "",
        "Simon London",
        "Simon London Route",
        "Lorena London",
        "Lorena London Route",
        "Bracknell Time",
        "What the Area is Like",
        "Walk to Town",
        "Walkable Amenities",
        "",
        "Primary School",
        "Primary Walk",
        "Primary Ofsted",
        "Primary Inspection Year",
        "Secondary School",
        "Secondary Walk",
        "Secondary Ofsted",
        "Secondary Inspection Year",
        "Secondary Bus",
        "Secondary Bus Route",
        "",
        "Monthly Mortgage Payment (£)",
        "Monthly Sinking Fund (£)",
        "Monthly Life Insurance (£)",
        "Monthly Commute Cost (£)",
        "Monthly Council Tax (£)",
        "Total Monthly Housing Cost (£)",
        "",
        "Ashby Works Estimate (£)",
        "Group Notes / WhatsApp",
        "Ashby comments",
        "Design Needed",
        "Planning Needed",
        "Status",
        "Status Reason",
    ]

    DATA_HEADERS = [
        "Rightmove URL",
        "Address",
        "Postcode",
        "Bedrooms",
        "Price (£)",
        "Actual Latitude",
        "Actual Longitude",
        "Rightmove ID",
        "Simon London (min)",
        "Simon London Cost (£)",
        "Simon London Route",
        "Simon Parking Cost (£)",
        "Lorena London (min)",
        "Lorena London Cost (£)",
        "Lorena London Route",
        "Bracknell Time (min)",
        "Bracknell Cost (£)",
        "Primary School",
        "Primary Distance (km)",
        "Primary Walk (min)",
        "Primary School Link",
        "Primary Ofsted",
        "Primary Inspection Year",
        "Secondary School",
        "Secondary Distance (km)",
        "Secondary Walk (min)",
        "Secondary School Link",
        "Secondary Ofsted",
        "Secondary Inspection Year",
        "Area Description",
        "Walk to Town (min)",
        "Walkable Amenities",
        "EPC Rating",
        "Council Tax Band",
        "Council Tax Cost (£)",
        "Secondary Bus (min)",
        "Secondary Bus Route",
        "Approx Latitude (est)",
        "Approx Longitude (est)",
        "Best Latitude",
        "Best Longitude",
        "Map URL",
        "Approx Station CRS",
        "Approx Station Name",
        "Stamp Duty (£)",
        "Net Ashby Contribution (£)",
        "Mortgage Required (£)",
        "Monthly Mortgage Payment (£)",
        "Yearly Sinking Fund (£)",
    ]

    def _make_enriched(self, rid: str, **overrides: dict) -> dict:
        """Return a minimal EnrichedProperty-compatible dict with real objects."""
        from houses.commute import Commute

        simon = Commute(
            destination_label="Simon (London)",
            destination_postcode="TE1 1ST",
            duration_minutes=30,
            daily_cost_gbp=10.0,
        )
        lorena = Commute(
            destination_label="Lorena (London)",
            destination_postcode="TE1 1ST",
            duration_minutes=45,
            daily_cost_gbp=12.0,
        )
        base = {
            "url": f"https://www.rightmove.co.uk/properties/{rid}",
            "address": "1 High Street, Test Town, TE1 1ST",
            "postcode": "TE1 1ST",
            "bedrooms": 3,
            "price": 300000.0,
            "simon_commute": simon,
            "lorena_commute": lorena,
            "petrol": Commute(
                destination_label="Bracknell Office (RG12 8YA)",
                destination_postcode="RG12 8YA",
                duration_minutes=90,
                daily_cost_gbp=12.50,
                mode="drive",
            ),
            "primary_school": None,
            "primary_school_commute": None,
            "primary_school_distance_km": None,
            "secondary_school": None,
            "secondary_school_commute": None,
            "secondary_school_distance_km": None,
            "town_description": "A nice town.",
            "walk_to_town_minutes": 10,
            "walkable_amenities": "Shops, cafe",
            "primary_ofsted": "Good",
            "secondary_ofsted": "Good",
            "primary_inspection_year": "2023",
            "secondary_inspection_year": "2022",
            "epc_rating": "C",
            "council_tax": CouncilTaxInfo(band="D", yearly_cost=1800.0),
            "commute_breakdown": CommuteBreakdown(yearly_total_gbp=4600.0),
            "approx_latitude": 51.5,
            "approx_longitude": -0.1,
            "approx_station_crs": "TST",
            "approx_station_name": "Test Station",
        }
        base.update(overrides)
        return base

    def _mock_sheet(
        self,
        view_rows: list[list[str]] | None = None,
        data_rows: list[list[str]] | None = None,
    ) -> MagicMock:
        """Build a mocked gspread sheet with View and Data tabs.

        The returned client tracks all writes via ``.written_cells`` and
        ``.appended_rows`` so tests can verify what was written to the sheet.
        """
        if view_rows is None:
            view_rows = []
        if data_rows is None:
            data_rows = []

        view_data = [self.VIEW_HEADERS] + view_rows
        data_data = [self.DATA_HEADERS] + data_rows

        mock_view_ws = MagicMock()
        mock_view_ws.get_all_values.return_value = view_data

        written_cells: list[dict] = []
        appended_rows: list[list[str]] = []

        mock_data_ws = MagicMock()
        mock_data_ws.get_all_values.return_value = data_data
        mock_data_ws.id = 12345

        # Capture appended rows
        def _append_row(values, value_input_option=""):
            appended_rows.append(values)

        mock_data_ws.append_row.side_effect = _append_row

        # Capture values_batch_update calls (used by Tab.batch_update)
        def _values_batch_update(body):
            for item in body.get("data", []):
                written_cells.append({"range": item["range"], "values": item["values"]})

        mock_data_ws.spreadsheet = MagicMock()
        mock_data_ws.spreadsheet.values_batch_update.side_effect = _values_batch_update
        mock_data_ws.spreadsheet.batch_update.return_value = None

        mock_sh = MagicMock()

        def _worksheet(name: str):
            if name == "Properties View":
                return mock_view_ws
            return mock_data_ws

        mock_sh.worksheet.side_effect = _worksheet

        mock_client = MagicMock()
        mock_client.open_by_key.return_value = mock_sh
        mock_client.written_cells = written_cells
        mock_client.appended_rows = appended_rows
        return mock_client

    def _build_view_row(self, address: str, url: str, rid: str = "") -> list[str]:
        """Quick helper for a View tab row."""
        row = [""] * len(self.VIEW_HEADERS)
        row[0] = address  # Listing Address
        row[1] = url  # Rightmove Link
        row[2] = ""  # Map (formula column)
        row[3] = rid or ""  # Rightmove ID
        return row

    def test_view_tab_empty(self):
        """When View tab has only headers, returns empty results."""
        original = settings.sheet_id
        settings.sheet_id = "fake-id"
        try:
            mock_client = self._mock_sheet(view_rows=[])
            with patch("houses.server.get_client", return_value=mock_client):
                resp = client.post("/properties")
            assert resp.status_code == 200
            assert self._parse_rows(resp) == []
        finally:
            settings.sheet_id = original

    def test_new_property_creates_row(self):
        """New property writes data to the correct row position (no append)."""
        original_id = settings.sheet_id
        original_sample = settings.rightmove_sample_page
        settings.sheet_id = "fake-id"
        settings.rightmove_sample_page = ""
        try:
            url = "https://www.rightmove.co.uk/properties/999999999"
            view_rows = [self._build_view_row("1 Test St, Test Town, TE1 1ST", url, "")]
            mock_client = self._mock_sheet(view_rows=view_rows)
            with patch("houses.server.get_client", return_value=mock_client):
                resp = client.post("/properties")
            assert resp.status_code == 200, resp.text
            results = self._parse_rows(resp)
            assert len(results) >= 1
            row_result = results[0]
            # The row at position 2 is empty → written in place
            assert row_result["status"] in ("updated", "created"), f"Expected created/updated, got {row_result}"
            assert row_result["rid"] == "999999999"

            # Verify the mock sheet recorded writes at the correct position
            cells = mock_client.written_cells
            assert cells, "No cells were written to the sheet"
            # Check that the URL and address appear in written ranges
            all_text = " ".join(str(c) for c in cells)
            assert url in all_text, f"URL {url} not found in written cells"
            assert "Test St" in all_text, "Address not found in written cells"
        finally:
            settings.sheet_id = original_id
            settings.rightmove_sample_page = original_sample

    def test_new_property_dry_run(self):
        """no_write runs enrichment but doesn't write to the sheet."""
        original_id = settings.sheet_id
        original_sample = settings.rightmove_sample_page
        settings.sheet_id = "fake-id"
        settings.rightmove_sample_page = ""
        try:
            url = "https://www.rightmove.co.uk/properties/888888888"
            view_rows = [self._build_view_row("2 Test St, Test Town, TE1 1ST", url, "")]
            mock_client = self._mock_sheet(view_rows=view_rows)
            with patch("houses.server.get_client", return_value=mock_client):
                resp = client.post("/properties?no_write=true")
            assert resp.status_code == 200
            results = self._parse_rows(resp)
            assert len(results) >= 1
            assert results[0]["status"] == "would_create"
            # No sheet writes happened (no_write=true)
            assert not mock_client.written_cells, "Cells were written despite no_write"
        finally:
            settings.sheet_id = original_id
            settings.rightmove_sample_page = original_sample

    def test_existing_fully_enriched_skips(self):
        """Existing property with all columns filled (incl. user columns) is skipped."""
        original = settings.sheet_id
        settings.sheet_id = "fake-id"
        try:
            rid = "111111111"
            url = f"https://www.rightmove.co.uk/properties/{rid}"
            view_rows = [self._build_view_row("3 Test St, Test Town, TE1 1ST", url, rid)]

            data_row = [""] * len(self.DATA_HEADERS)
            # Fill EVERY column so nothing is empty
            for idx in range(len(self.DATA_HEADERS)):
                data_row[idx] = "filled"
            # Set the Rightmove ID after the fill so it isn't overwritten
            rid_idx = self.DATA_HEADERS.index("Rightmove ID")
            data_row[rid_idx] = rid

            mock_client = self._mock_sheet(view_rows=view_rows, data_rows=[data_row])
            with (
                patch("houses.server.get_client", return_value=mock_client),
                patch("houses.server.run_backfill_enrichment") as mock_enrich,
            ):
                mock_enrich.return_value = EnrichedProperty(
                    **self._make_enriched(rid, address="3 Test St, Test Town, TE1 1ST"),
                )
                resp = client.post("/properties")
            assert resp.status_code == 200
            results = self._parse_rows(resp)
            assert len(results) == 1
            assert results[0]["status"] == "skipped"
            assert results[0]["reason"] == "already fully enriched"
        finally:
            settings.sheet_id = original

    def test_existing_partial_updates_empty_cells_only(self):
        """Existing property with some empty enrichment cells is updated."""
        original = settings.sheet_id
        settings.sheet_id = "fake-id"
        try:
            rid = "222222222"
            url = f"https://www.rightmove.co.uk/properties/{rid}"
            view_rows = [self._build_view_row("4 Test St, Test Town, TE1 1ST", url, rid)]

            # Build a Data row with simon/lorena mostly filled, but Simon Parking
            # Cost empty. This triggers simon enrichment and tests that the
            # backfill passes lookup=pc (not lookup=address) to the enrichment.
            data_row = [""] * len(self.DATA_HEADERS)
            data_row[self.DATA_HEADERS.index("Rightmove ID")] = rid
            filled_cols = [
                "Simon London (min)",
                "Simon London Cost (£)",
                "Simon London Route",
                # Simon Parking Cost (£) deliberately left empty
                "Lorena London (min)",
                "Lorena London Cost (£)",
                "Lorena London Route",
            ]
            for col in filled_cols:
                data_row[self.DATA_HEADERS.index(col)] = "filled"

            mock_client = self._mock_sheet(view_rows=view_rows, data_rows=[data_row])
            with (
                patch("houses.server.get_client", return_value=mock_client),
                patch("houses.server.run_backfill_enrichment") as mock_enrich,
                patch("houses.server._write_backfill_cells") as mock_write,
            ):
                mock_enrich.return_value = EnrichedProperty(**self._make_enriched(rid))
                resp = client.post("/properties")
            assert resp.status_code == 200
            results = self._parse_rows(resp)
            assert len(results) == 1
            assert results[0]["status"] == "updated"
            # Simon Parking Cost is empty → simon enrichment triggered
            assert "simon" in results[0]["fields"]
            assert "lorena" not in results[0]["fields"]
            assert "petrol" in results[0]["fields"]
            assert "schools" in results[0]["fields"]
            mock_enrich.assert_called_once()
            mock_write.assert_called_once()
            # lookup=None means _run_enrichment computes the best string
            # (address + full postcode when available)
            _, enrich_kwargs = mock_enrich.call_args
            assert enrich_kwargs["lookup"] is None, f"backfill passed lookup={enrich_kwargs['lookup']!r}, expected None"
            # Verify write call includes Simon Parking Cost (the only empty simon col)
            args, _ = mock_write.call_args
            allowed = args[6]  # allowed_headers positional arg
            assert "Simon Parking Cost (£)" in allowed
            assert "Simon London (min)" not in allowed  # filled → not in allowed
            assert "Lorena London (min)" not in allowed
            assert "Bracknell Cost (£)" in allowed
        finally:
            settings.sheet_id = original

    def test_existing_partial_dry_run(self):
        """no_write for an existing partial property enriches but doesn't write."""
        original = settings.sheet_id
        settings.sheet_id = "fake-id"
        try:
            rid = "333333333"
            url = f"https://www.rightmove.co.uk/properties/{rid}"
            view_rows = [self._build_view_row("5 Test St, Test Town, TE1 1ST", url, rid)]

            data_row = [""] * len(self.DATA_HEADERS)
            data_row[self.DATA_HEADERS.index("Rightmove ID")] = rid
            # Only petrol filled, everything else empty
            data_row[self.DATA_HEADERS.index("Bracknell Cost (£)")] = "10.00"

            mock_client = self._mock_sheet(view_rows=view_rows, data_rows=[data_row])
            enriched_fake = EnrichedProperty(
                **self._make_enriched(rid, address="5 Test St, Test Town, TE1 1ST"),
            )
            with (
                patch("houses.server.get_client", return_value=mock_client),
                patch("houses.server.run_backfill_enrichment", return_value=enriched_fake),
                patch("houses.server._write_backfill_cells") as mock_write,
            ):
                resp = client.post("/properties?no_write=true")
            assert resp.status_code == 200
            results = self._parse_rows(resp)
            assert len(results) == 1
            assert results[0]["status"] == "would_update"
            mock_write.assert_not_called()
        finally:
            settings.sheet_id = original

    def test_fields_filter_restricts_enrichment(self):
        """Query param fields= limits which enrichment modules run."""
        original = settings.sheet_id
        settings.sheet_id = "fake-id"
        try:
            rid = "444444444"
            url = f"https://www.rightmove.co.uk/properties/{rid}"
            view_rows = [self._build_view_row("6 Test St, Test Town, TE1 1ST", url, rid)]

            data_row = [""] * len(self.DATA_HEADERS)
            data_row[self.DATA_HEADERS.index("Rightmove ID")] = rid

            mock_client = self._mock_sheet(view_rows=view_rows, data_rows=[data_row])
            with (
                patch("houses.server.get_client", return_value=mock_client),
                patch("houses.server.run_backfill_enrichment") as mock_enrich,
                patch("houses.server._write_backfill_cells") as mock_write,
            ):
                mock_enrich.return_value = EnrichedProperty(**self._make_enriched(rid))
                resp = client.post("/properties?fields=epc&fields=council_tax")
            assert resp.status_code == 200
            results = self._parse_rows(resp)
            assert len(results) == 1
            assert results[0]["status"] == "updated"
            # Only the fields specified in query param should have run
            assert set(results[0]["fields"]) == {"epc", "council_tax"}
            mock_enrich.assert_called_once()
            mock_write.assert_called_once()
        finally:
            settings.sheet_id = original

    @pytest.mark.integration
    def test_user_columns_filled_for_new_property(self):
        """User columns (URL, Address, Postcode, Bedrooms) are populated
        when creating a new Data tab row from backfill."""
        original_id = settings.sheet_id
        original_sample = settings.rightmove_sample_page
        settings.sheet_id = "fake-id"
        fixture_dir = Path(__file__).resolve().parent.parent / "fixtures"
        settings.rightmove_sample_page = str(fixture_dir / "rightmove_sample.html")
        try:
            url = "https://www.rightmove.co.uk/properties/555555555"
            view_rows = [self._build_view_row("1 Test Road, TE1 1ST", url, "")]
            mock_client = self._mock_sheet(view_rows=view_rows)

            with patch("houses.server.get_client", return_value=mock_client):
                resp = client.post("/properties")
            assert resp.status_code == 200, resp.text

            cells = mock_client.written_cells
            assert cells, "No cells were written to the sheet"
            all_text = " ".join(str(c) for c in cells)
            assert url in all_text, f"URL {url} not found in written cells"
            assert "Test Road" in all_text, "Address not found in written cells"
        finally:
            settings.sheet_id = original_id
            settings.rightmove_sample_page = original_sample

    @pytest.mark.integration
    def test_user_columns_passed_to_write_backfill(self):
        """When updating an existing row, user columns are written when empty."""
        original_id = settings.sheet_id
        original_sample = settings.rightmove_sample_page
        settings.sheet_id = "fake-id"
        fixture_dir = Path(__file__).resolve().parent.parent / "fixtures"
        settings.rightmove_sample_page = str(fixture_dir / "rightmove_sample.html")
        try:
            rid = "666666666"
            url = f"https://www.rightmove.co.uk/properties/{rid}"
            view_rows = [self._build_view_row("2 Test Lane, TE2 2ST", url, rid)]

            # Seed Data tab with existing row — user columns left empty
            data_row = [""] * len(self.DATA_HEADERS)
            data_row[self.DATA_HEADERS.index("Rightmove ID")] = rid

            mock_client = self._mock_sheet(view_rows=view_rows, data_rows=[data_row])

            with patch("houses.server.get_client", return_value=mock_client):
                resp = client.post("/properties")
            assert resp.status_code == 200, resp.text

            # Verify user columns were written to the sheet
            cells = mock_client.written_cells
            assert cells, "No cells were written to the sheet"
            all_text = " ".join(str(c) for c in cells)
            assert url in all_text, f"URL {url} not found in written cells"
            assert "Test Lane" in all_text, "Address not found in written cells"
        finally:
            settings.sheet_id = original_id
            settings.rightmove_sample_page = original_sample

    def test_epc_lookup_guard_via_enrichment(self):
        """_run_enrichment passes the address to the EPC service."""
        fake_epc = FakeEPC(band="C")
        services = Services(epc_service=fake_epc)
        result = asyncio.run(
            run_backfill_enrichment(
                url="https://www.rightmove.co.uk/properties/123",
                address="7 Sandy Close, Woking, GU22",
                postcode="GU22 8BQ",
                lookup="GU22 8BQ",
                bedrooms=None,
                price=None,
                enabled={"epc"},
                services=services,
            )
        )
        assert result.epc_rating == "C"
        assert fake_epc.calls == [("GU22 8BQ", "7 Sandy Close, Woking, GU22")]

    @pytest.mark.asyncio
    async def test_get_drive_minutes_with_geocode_fallback(self, _mock_http_requests, monkeypatch):
        """_get_drive_minutes must handle GeoPoint from geocoding fallback.

        When _lookup_station_coords fails (station not in CSV), the geocoding
        fallback returns a GeoPoint. The function then tries ``dest_coords[1]``
        which raises ``TypeError: 'GeoPoint' object is not subscriptable``.
        """
        from houses.transit_route import _get_drive_minutes

        result = await _get_drive_minutes("KT13 8XG", "Nonexistent Station XYZ")
        # Should not crash — may return None if geocoding fails too
        assert result is None or isinstance(result, (int, float))

    @pytest.mark.asyncio
    async def test_park_and_ride_does_not_crash(self, _mock_http_requests, monkeypatch):
        """Park-and-ride's parking cost lookup must not crash with TypeError.

        Regression test for missing ``await`` in ``_add_parking_cost``.
        Before the fix, calling ``_add_parking_cost`` with a journey whose
        first leg is "driving" raised::

            TypeError: unsupported operand type(s) for: +'float' and 'coroutine'
        """
        from houses.transit_route import TransitRoute

        # Create a TransitRoute instance with a mocked plan that calls
        # _add_parking_cost directly with a "driving" first leg.
        route = TransitRoute("SL6", "SW1V 2QQ", "test", park_and_ride=True)
        cost = 5.0  # initial daily_cost_gbp

        # _add_parking_cost checks if the first leg is "driving" and then
        # uses CarParkRegistry to look up the cost via CRS fallback.
        # The data must have a journey with a "driving" first leg that has
        # an arrivalPoint with a station name we have a parking rate for.
        data = {
            "journeys": [
                {
                    "duration": 87,
                    "legs": [
                        {
                            "mode": {"name": "driving"},
                            "duration": 15,
                            "isTimeline": True,
                            "arrivalPoint": {"commonName": "Maidenhead Rail Station"},
                        },
                        {"mode": {"name": "train", "isTimeline": True}, "duration": 30},
                    ],
                    "fare": {"totalCost": 500, "singleFare": 250},
                }
            ]
        }

        # Monkeypatch the parking rates path to a known CSV
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "parking_rates.csv"
            csv_path.write_text("station_name,crs,daily_cost_gbp\nMaidenhead Rail Station,MAI,8.50\n")
            monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

            # Call _add_parking_cost directly — this is what triggers the bug
            result = await route._add_parking_cost(data, cost)

        # Should return a tuple of (parking_cost, new_total_cost)
        assert result is not None, "_add_parking_cost should not crash"
        assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"

    # ── Force vs no-force batch refresh ────────────────────────────────
    #
    # ``force`` controls whether existing cell values are overwritten.
    # ``fields`` restricts which column groups to consider.
    # The two are orthogonal.

    @pytest.mark.integration
    def test_force_true_refreshes_filled_simon_cells(self):
        """``force=true`` + ``fields=simon`` enriches even filled simon columns."""
        original = settings.sheet_id
        settings.sheet_id = "fake-id"
        try:
            rid = "777777771"
            url = f"https://www.rightmove.co.uk/properties/{rid}"
            view_rows = [self._build_view_row("7 Test St, TE1 1ST", url, rid)]

            data_row = [""] * len(self.DATA_HEADERS)
            data_row[self.DATA_HEADERS.index("Rightmove ID")] = rid
            # Fill ALL simon columns so nothing is empty
            for col in self.DATA_HEADERS:
                ef = header_to_enrichment_field(col)
                if ef == "simon":
                    data_row[self.DATA_HEADERS.index(col)] = "filled"

            mock_client = self._mock_sheet(view_rows=view_rows, data_rows=[data_row])
            with (
                patch("houses.server.get_client", return_value=mock_client),
                patch("houses.server.run_backfill_enrichment") as mock_enrich,
                patch("houses.server._write_backfill_cells"),
            ):
                mock_enrich.return_value = EnrichedProperty(**self._make_enriched(rid))
                resp = client.post("/properties?fields=simon&force=true")
            assert resp.status_code == 200
            results = self._parse_rows(resp)
            assert len(results) == 1
            assert results[0]["status"] == "updated", f"Expected updated, got {results[0]}"
            mock_enrich.assert_called_once()
        finally:
            settings.sheet_id = original

    @pytest.mark.integration
    def test_force_false_skips_filled_simon_cells(self):
        """``force=false`` (default) + ``fields=simon`` skips filled simon columns."""
        original = settings.sheet_id
        settings.sheet_id = "fake-id"
        try:
            rid = "777777772"
            url = f"https://www.rightmove.co.uk/properties/{rid}"
            view_rows = [self._build_view_row("8 Test St, TE1 1ST", url, rid)]

            data_row = [""] * len(self.DATA_HEADERS)
            data_row[self.DATA_HEADERS.index("Rightmove ID")] = rid
            for col in self.DATA_HEADERS:
                ef = header_to_enrichment_field(col)
                if ef == "simon":
                    data_row[self.DATA_HEADERS.index(col)] = "filled"

            mock_client = self._mock_sheet(view_rows=view_rows, data_rows=[data_row])
            with (
                patch("houses.server.get_client", return_value=mock_client),
                patch("houses.server.run_backfill_enrichment") as mock_enrich,
            ):
                mock_enrich.return_value = EnrichedProperty(**self._make_enriched(rid))
                resp = client.post("/properties?fields=simon")
            assert resp.status_code == 200
            results = self._parse_rows(resp)
            assert len(results) == 1
            assert results[0]["status"] == "skipped", f"Expected skipped (fully enriched), got {results[0]}"
            mock_enrich.assert_not_called()
        finally:
            settings.sheet_id = original

    def test_lookup_derived_when_empty_with_address_and_postcode(self):
        """When lookup='' but address+postcode are provided, lookup should be
        derived from the postcode (not left as empty string)."""
        fake_router = FakeCommuteRouter(
            simon=Commute(destination_label="S", destination_postcode="SW1V 2QQ", duration_minutes=45),
            lorena=Commute(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=30),
        )
        services = Services(commute_router=fake_router)

        result = asyncio.run(
            run_backfill_enrichment(
                url="https://www.rightmove.co.uk/properties/999",
                address="Some Road, Maidenhead",
                postcode="SL6 3YZ",
                lookup="",
                bedrooms=None,
                price=None,
                enabled={"simon", "lorena"},
                services=services,
            )
        )

        # Should have used the full postcode, not empty string
        assert fake_router.calls == [("simon", "SL6 3YZ"), ("lorena", "SL6 3YZ")]
        assert result.simon_commute.duration_minutes == 45
