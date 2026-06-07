"""Tests for the FastAPI server endpoints."""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, Client, MockTransport, Response

from houses.config import settings
from houses.models import EnrichedProperty, TransitInfo
from houses.server import _run_backfill_enrichment, app, extract_postcode
from houses.sheets import COLUMN_HEADERS, VIEW_HEADERS

client = TestClient(app)


class TestExtractPostcode:
    def test_full_postcode(self):
        assert extract_postcode("High Street, Some Town, RG14 1AA") == "RG14 1AA"

    def test_outcode_only(self):
        assert extract_postcode("Shoppenhangers Road, Maidenhead, SL6") == "SL6"

    def test_no_postcode(self):
        assert extract_postcode("Some Road, Town") == ""

    def test_empty_string(self):
        assert extract_postcode("") == ""

    def test_postcode_at_start(self):
        assert extract_postcode("SW1A 1AA London") == "SW1A 1AA"

    def test_london_outcode(self):
        assert extract_postcode("Victoria Street, London, SW1E") == "SW1E"


class TestHealth:
    def test_health_returns_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


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
        resp = client.post("/inject-property", json=self.VALID_PAYLOAD)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "data" in body

    @pytest.mark.integration
    def test_minimal_payload_with_only_url(self):
        resp = client.post("/inject-property", json={"url": "https://www.rightmove.co.uk/properties/1"})
        assert resp.status_code == 200
        assert resp.json()["data"]["postcode"] == ""

    @pytest.mark.integration
    def test_accepts_any_url(self):
        payload = {**self.VALID_PAYLOAD, "url": "https://example.com/"}
        resp = client.post("/inject-property", json=payload)
        assert resp.status_code == 200

    def test_rejects_invalid_types(self):
        payload = {**self.VALID_PAYLOAD, "bedrooms": "three"}
        resp = client.post("/inject-property", json=payload)
        assert resp.status_code == 422

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
            with patch("houses.sheets.get_client", return_value=mock_client):
                resp = client.post(
                    "/inject-property",
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
        resp = client.post("/inject-property", json=self.VALID_PAYLOAD)
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
        resp = client.post("/inject-property", json=self.MAIDENHEAD_PAYLOAD)
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
        assert petrol.get("cost_gbp") is not None, f"Petrol missing: {petrol}"
        assert data.get("primary_school") is not None, "No primary school"
        assert data.get("secondary_school") is not None, "No secondary school"


class TestBackfillView:
    """Tests for POST /backfill-view."""

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
        "Rightmove ID",
        "Purchase Cost (£)",
        "EPC Rating",
        "Yearly Commute Total (£)",
        "Yearly Council Tax (£)",
        "Simon London",
        "Simon London Route",
        "Lorena London",
        "Lorena London Route",
        "Bracknell Time",
        "What the Area is Like",
        "Walk to Town",
        "Walkable Amenities",
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
        "Group Notes / WhatsApp",
        "Ashby comments",
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
        "Approx Station CRS",
        "Approx Station Name",
    ]

    def _make_enriched(self, rid: str, **overrides: dict) -> dict:
        """Return a minimal EnrichedProperty-compatible dict."""
        simon = {
            "destination_label": "Simon (London)",
            "destination_postcode": "TE1 1ST",
            "duration_minutes": 30,
            "daily_cost_gbp": 10.0,
        }
        lorena = {
            "destination_label": "Lorena (London)",
            "destination_postcode": "TE1 1ST",
            "duration_minutes": 45,
            "daily_cost_gbp": 12.0,
        }
        base = {
            "url": f"https://www.rightmove.co.uk/properties/{rid}",
            "address": "1 High Street, Test Town, TE1 1ST",
            "postcode": "TE1 1ST",
            "bedrooms": 3,
            "price": 300000.0,
            "simon_commute": simon,
            "lorena_commute": lorena,
            "petrol": {"round_trip_km": 80.0, "round_trip_minutes": 90, "cost_gbp": 12.50},
            "primary_school": {"name": "Test Primary", "type": "primary", "distance_km": 0.5, "urn": "100001"},
            "secondary_school": {"name": "Test Secondary", "type": "secondary", "distance_km": 1.2, "urn": "100002"},
            "town_description": "A nice town.",
            "walk_to_town_minutes": 10,
            "walkable_amenities": "Shops, cafe",
            "primary_ofsted": "Good",
            "secondary_ofsted": "Good",
            "primary_inspection_year": "2023",
            "secondary_inspection_year": "2022",
            "epc_rating": "C",
            "council_tax": {"band": "D", "yearly_cost": 1800.0},
            "commute_breakdown": {"yearly_total_gbp": 4600.0},
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
        row[2] = rid or ""  # Rightmove ID
        return row

    def test_no_sheet_id(self):
        """When HOUSES_SHEET_ID is not set, returns empty results."""
        original = settings.sheet_id
        settings.sheet_id = ""
        try:
            resp = client.post("/backfill-view")
            assert resp.status_code == 200
            assert self._parse_rows(resp) == []
        finally:
            settings.sheet_id = original

    def test_view_tab_empty(self):
        """When View tab has only headers, returns empty results."""
        original = settings.sheet_id
        settings.sheet_id = "fake-id"
        try:
            mock_client = self._mock_sheet(view_rows=[])
            with patch("houses.server.get_client", return_value=mock_client):
                resp = client.post("/backfill-view")
            assert resp.status_code == 200
            assert self._parse_rows(resp) == []
        finally:
            settings.sheet_id = original

    def test_skips_row_without_rightmove_id(self):
        """View rows with no Rightmove URL or ID are skipped."""
        original = settings.sheet_id
        settings.sheet_id = "fake-id"
        try:
            view_rows = [self._build_view_row("No URL or ID", "", "")]
            mock_client = self._mock_sheet(view_rows=view_rows)
            with patch("houses.server.get_client", return_value=mock_client):
                resp = client.post("/backfill-view")
            assert resp.status_code == 200
            results = self._parse_rows(resp)
            assert len(results) == 1
            assert results[0]["status"] == "skipped"
            assert results[0]["reason"] == "no Rightmove ID"
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
            counter, async_patch, sync_patch = self._mock_httpx()
            with async_patch, sync_patch, patch("houses.server.get_client", return_value=mock_client):
                resp = client.post("/backfill-view")
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
        """Dry run reports would_create without calling enrichment or sheet writes."""
        original_id = settings.sheet_id
        original_sample = settings.rightmove_sample_page
        settings.sheet_id = "fake-id"
        settings.rightmove_sample_page = ""
        try:
            url = "https://www.rightmove.co.uk/properties/888888888"
            view_rows = [self._build_view_row("2 Test St, Test Town, TE1 1ST", url, "")]
            mock_client = self._mock_sheet(view_rows=view_rows)
            counter, async_patch, sync_patch = self._mock_httpx()
            with async_patch, sync_patch, patch("houses.server.get_client", return_value=mock_client):
                resp = client.post("/backfill-view?dry_run=true")
            assert resp.status_code == 200
            results = self._parse_rows(resp)
            assert len(results) >= 1
            assert results[0]["status"] == "would_create"
            assert self._parse_start(resp)["dry_run"] is True

            # No enrichment or sheet writes happened
            assert not mock_client.written_cells, "Cells were written despite dry_run"
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
                patch("houses.server._run_backfill_enrichment") as mock_enrich,
            ):
                mock_enrich.return_value = EnrichedProperty(
                    **self._make_enriched(rid, address="3 Test St, Test Town, TE1 1ST"),
                )
                resp = client.post("/backfill-view")
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

            # Build a Data row with simon/lorena fully filled, other enriched cols empty
            data_row = [""] * len(self.DATA_HEADERS)
            data_row[self.DATA_HEADERS.index("Rightmove ID")] = rid
            filled_cols = [
                "Simon London (min)", "Simon London Cost (£)", "Simon London Route",
                "Lorena London (min)", "Lorena London Cost (£)", "Lorena London Route",
            ]
            for col in filled_cols:
                data_row[self.DATA_HEADERS.index(col)] = "filled"

            mock_client = self._mock_sheet(view_rows=view_rows, data_rows=[data_row])
            with (
                patch("houses.server.get_client", return_value=mock_client),
                patch("houses.server._run_backfill_enrichment") as mock_enrich,
                patch("houses.server._write_backfill_cells") as mock_write,
            ):
                mock_enrich.return_value = EnrichedProperty(**self._make_enriched(rid))
                resp = client.post("/backfill-view")
            assert resp.status_code == 200
            results = self._parse_rows(resp)
            assert len(results) == 1
            assert results[0]["status"] == "updated"
            # Should have run only non-simon/lorena fields
            assert "simon" not in results[0]["fields"]
            assert "lorena" not in results[0]["fields"]
            assert "petrol" in results[0]["fields"]
            assert "schools" in results[0]["fields"]
            mock_enrich.assert_called_once()
            mock_write.assert_called_once()
            # Verify write call skips simon/lorena; gets only empty-enriched headers
            args, _ = mock_write.call_args
            allowed = args[6]  # allowed_headers positional arg
            assert "Simon London (min)" not in allowed
            assert "Bracknell Cost (£)" in allowed
        finally:
            settings.sheet_id = original

    def test_existing_partial_dry_run(self):
        """Dry run for an existing partial property reports would_update without enrichment."""
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
            with (
                patch("houses.server.get_client", return_value=mock_client),
                patch("houses.server._run_backfill_enrichment") as mock_enrich,
                patch("houses.server._write_backfill_cells") as mock_write,
            ):
                resp = client.post("/backfill-view?dry_run=true")
            assert resp.status_code == 200
            results = self._parse_rows(resp)
            assert len(results) == 1
            assert results[0]["status"] == "would_update"
            assert self._parse_start(resp)["dry_run"] is True
            mock_enrich.assert_not_called()
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
                patch("houses.server._run_backfill_enrichment") as mock_enrich,
                patch("houses.server._write_backfill_cells") as mock_write,
            ):
                mock_enrich.return_value = EnrichedProperty(**self._make_enriched(rid))
                resp = client.post("/backfill-view?fields=epc&fields=council_tax")
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

    def _mock_httpx(self):
        """Context manager that patches both ``httpx.AsyncClient`` and
        ``httpx.Client`` with a ``MockTransport`` that returns synthetic
        responses for every external API the enrichment pipeline calls.

        Yields the handler's call-count dict so tests can verify which
        APIs were hit (or not hit).
        """

        class _Counter:
            def __init__(self):
                self.calls: list[str] = []

            def handler(self, request):
                url = str(request.url)
                self.calls.append(url)

                # TfL
                if "tfl.gov.uk/Journey/JourneyResults" in url:
                    return Response(
                        200, json={"journeys": [{"duration": 30, "fare": {"totalCost": 500}}]}
                    )
                # postcodes.io
                if "api.postcodes.io" in url:
                    return Response(200, json={"status": 200, "result": {"latitude": 51.5, "longitude": -0.1}})
                # ORS Directions (driving or walking)
                if "openrouteservice.org/v2/directions" in url:
                    return Response(200, json={"routes": [{"summary": {"distance": 50, "duration": 1800}}]})
                # ORS Geocode
                if "openrouteservice.org/geocode" in url:
                    return Response(
                        200, json={"features": [{"geometry": {"coordinates": [-0.1, 51.5]}}]}
                    )
                # Google Maps Geocode
                if "maps.googleapis.com/maps/api/geocode" in url:
                    return Response(
                        200, json={"results": [{"geometry": {"location": {"lat": 51.5, "lng": -0.1}}}]}
                    )
                # Google Places
                if "places.googleapis.com" in url:
                    return Response(200, json={"places": []})
                # Google Routes
                if "routes.googleapis.com" in url:
                    return Response(200, json={"routes": [{"legs": [{"duration": "1800s"}]}]})
                # EPC
                if "get-energy-performance-data" in url:
                    return Response(
                        200,
                        json={"data": [{"currentEnergyEfficiencyBand": "C", "registrationDate": "2023-01-01"}]},
                    )
                # CivAccount
                if "civaccount.co.uk" in url:
                    return Response(200, json={"band_d_rate": 1500.0})
                # Nominatim
                if "nominatim.openstreetmap.org" in url:
                    return Response(200, json=[{"lat": "51.5", "lon": "-0.1"}])
                # OpenRouter LLM
                if "openrouter.ai" in url:
                    return Response(200, json={"choices": [{"message": {"content": "A pleasant town."}}]})
                # Overpass
                if "overpass-api.de" in url:
                    return Response(200, json={"elements": []})
                # VOA council tax band search
                if "tax.service.gov.uk" in url or "voa" in url.lower() or "get-information-schools" in url:
                    return Response(200, json={})

                logger = __import__("logging").getLogger("test")
                logger.warning("Unhandled httpx request: %s %s", request.method, url)
                return Response(404)

        counter = _Counter()

        def _patch_client(original_init, handler):
            def patched_init(self, **kwargs):
                kwargs.setdefault("transport", MockTransport(handler))
                original_init(self, **kwargs)
            return patched_init

        original_async_init = AsyncClient.__init__
        original_sync_init = Client.__init__

        async_patch = patch.object(AsyncClient, "__init__", _patch_client(original_async_init, counter.handler))
        sync_patch = patch.object(Client, "__init__", _patch_client(original_sync_init, counter.handler))

        return counter, async_patch, sync_patch

    @pytest.mark.integration
    def test_user_columns_filled_for_new_property(self):
        """User columns (URL, Address, Postcode, Bedrooms) are populated
        when creating a new Data tab row from backfill.

        Uses the real test sheet and real enrichment (APIs mocked at the
        httpx transport layer).
        """
        original_id = settings.sheet_id
        original_sample = settings.rightmove_sample_page
        settings.sheet_id = settings.test_sheet_id
        fixture_dir = Path(__file__).resolve().parent.parent / "fixtures"
        settings.rightmove_sample_page = str(fixture_dir / "rightmove_sample.html")
        try:
            # Clean test sheet before starting
            from houses.sheets import get_client as real_client

            gclient = real_client()
            sh = gclient.open_by_key(settings.sheet_id)
            data_ws = sh.worksheet("Properties Data")
            view_ws = sh.worksheet("Properties View")
            # Clear existing data (keep header row)
            data_ws.clear()
            data_ws.append_row(self.DATA_HEADERS, value_input_option="USER_ENTERED")
            view_ws.clear()
            view_ws.append_row(self.VIEW_HEADERS, value_input_option="USER_ENTERED")

            # Add a View tab row with known data
            url = "https://www.rightmove.co.uk/properties/555555555"
            view_row = self._build_view_row("1 Test Road, TE1 1ST", url, "")
            view_ws.append_row(view_row, value_input_option="USER_ENTERED")

            counter, async_patch, sync_patch = self._mock_httpx()
            with async_patch, sync_patch:
                resp = client.post("/backfill-view")
            assert resp.status_code == 200, resp.text

            # Read back from the test Data tab to verify user columns were written
            data_rows = data_ws.get_all_values()
            assert len(data_rows) > 1, "Data tab has no rows after backfill"
            data_headers = data_rows[0]
            url_idx = data_headers.index("Rightmove URL")
            addr_idx = data_headers.index("Address")
            pc_idx = data_headers.index("Postcode")
            beds_idx = data_headers.index("Bedrooms")

            written = data_rows[1]
            assert written[url_idx] == url, f"URL: expected {url!r}, got {written[url_idx]!r}"
            assert "Test Road" in written[addr_idx], f"Address: expected 'Test Road' in {written[addr_idx]!r}"
            assert written[pc_idx] == "OX11 7EB", f"Postcode: expected OX11 7EB, got {written[pc_idx]!r}"
            assert written[beds_idx] == "5", f"Bedrooms: expected 5, got {written[beds_idx]!r}"
        finally:
            settings.sheet_id = original_id
            settings.rightmove_sample_page = original_sample

    @pytest.mark.integration
    def test_user_columns_passed_to_write_backfill(self):
        """When updating an existing row, user columns are written when empty.

        Uses the real test sheet and real enrichment (APIs mocked at the
        httpx transport layer).
        """
        original_id = settings.sheet_id
        original_sample = settings.rightmove_sample_page
        settings.sheet_id = settings.test_sheet_id
        fixture_dir = Path(__file__).resolve().parent.parent / "fixtures"
        settings.rightmove_sample_page = str(fixture_dir / "rightmove_sample.html")
        try:
            from houses.sheets import get_client as real_client

            gclient = real_client()
            sh = gclient.open_by_key(settings.sheet_id)
            data_ws = sh.worksheet("Properties Data")
            view_ws = sh.worksheet("Properties View")

            # Clear existing data (keep header row)
            data_ws.clear()
            data_ws.append_row(self.DATA_HEADERS, value_input_option="USER_ENTERED")
            view_ws.clear()
            view_ws.append_row(self.VIEW_HEADERS, value_input_option="USER_ENTERED")

            # Seed the Data tab with an existing row — user columns left empty
            rid = "666666666"
            url = f"https://www.rightmove.co.uk/properties/{rid}"
            data_row = [""] * len(self.DATA_HEADERS)
            rid_col = self.DATA_HEADERS.index("Rightmove ID")
            data_row[rid_col] = rid
            # Fill enriched columns so they are not empty (user columns stay empty)
            for idx in range(len(self.DATA_HEADERS)):
                if idx != rid_col:
                    data_row[idx] = "filled"
            data_row[rid_col] = rid  # restore RID after fill
            # Clear the user columns we expect to be auto-filled
            for col in ["Rightmove URL", "Address", "Postcode", "Bedrooms", "Price (£)"]:
                data_row[self.DATA_HEADERS.index(col)] = ""
            data_ws.append_row(data_row, value_input_option="USER_ENTERED")

            # Seed the View tab
            view_row = self._build_view_row("2 Test Lane, TE2 2ST", url, rid)
            view_ws.append_row(view_row, value_input_option="USER_ENTERED")

            counter, async_patch, sync_patch = self._mock_httpx()
            with async_patch, sync_patch:
                resp = client.post("/backfill-view")
            assert resp.status_code == 200, resp.text

            # Re-read the Data tab — user columns should be written
            rows = data_ws.get_all_values()
            assert len(rows) > 1

            data_headers = rows[0]
            addr_idx = data_headers.index("Address")
            url_idx = data_headers.index("Rightmove URL")
            pc_idx = data_headers.index("Postcode")

            updated = rows[1]
            assert updated[url_idx] == url, f"URL: expected {url!r}, got {updated[url_idx]!r}"
            assert "Test Lane" in updated[addr_idx], f"Address: expected 'Test Lane' in {updated[addr_idx]!r}"
            assert updated[pc_idx] == "OX11 7EB", f"Postcode: expected OX11 7EB, got {updated[pc_idx]!r}"
        finally:
            settings.sheet_id = original_id
            settings.rightmove_sample_page = original_sample

    def test_epc_skipped_without_house_number(self):
        """EPC lookup requires a house-numbered street address.

        Addresses like ``Nightingale Way, Denham Green`` (no number)
        should skip EPC even with a full postcode. Only addresses
        starting with a digit (e.g. ``7 Sandy Close, Woking``)
        should trigger the lookup.
        """
        original_id = settings.sheet_id
        settings.sheet_id = "fake-id"
        try:
            counter, async_patch, sync_patch = self._mock_httpx()
            with (
                async_patch,
                sync_patch,
                patch("houses.server.lookup_epc") as mock_epc,
            ):
                mock_epc.return_value = "C"

                # Address WITH house number → EPC should be called
                result = asyncio.run(
                    _run_backfill_enrichment(
                        url="https://www.rightmove.co.uk/properties/123",
                        address="7 Sandy Close, Woking, GU22",
                        postcode="GU22 8BQ",
                        lookup="GU22 8BQ",
                        bedrooms=None,
                        price=None,
                        enabled={"epc"},
                    )
                )
                assert result.epc_rating == "C", "EPC should have been looked up"
                mock_epc.assert_called()

                # Address WITHOUT house number → EPC should NOT be called
                mock_epc.reset_mock()
                result2 = asyncio.run(
                    _run_backfill_enrichment(
                        url="https://www.rightmove.co.uk/properties/456",
                        address="Nightingale Way, Denham Green",
                        postcode="UB9 5JH",
                        lookup="UB9 5JH",
                        bedrooms=None,
                        price=None,
                        enabled={"epc"},
                    )
                )
                assert result2.epc_rating == "", "EPC should NOT have been looked up"
                mock_epc.assert_not_called()
        finally:
            settings.sheet_id = original_id

    def test_lookup_derived_when_empty_with_address_and_postcode(self):
        """When lookup='' but address+postcode are provided, lookup should be
        derived from the postcode (not left as empty string)."""
        with (
            patch("houses.server.compute_simon_commute") as mock_simon,
            patch("houses.server.compute_lorena_commute") as mock_lorena,
        ):
            mock_simon.return_value = TransitInfo(
                destination_label="S", destination_postcode="SW1V 2QQ",
                duration_minutes=45,
            )
            mock_lorena.return_value = TransitInfo(
                destination_label="L", destination_postcode="EC3A 7LP",
                duration_minutes=30,
            )

            result = asyncio.run(
                _run_backfill_enrichment(
                    url="https://www.rightmove.co.uk/properties/999",
                    address="Some Road, Maidenhead",
                    postcode="SL6 3YZ",
                    lookup="",
                    bedrooms=None,
                    price=None,
                    enabled={"simon", "lorena"},
                )
            )

            # Should have used the full postcode, not empty string
            mock_simon.assert_called_once_with("SL6 3YZ")
            mock_lorena.assert_called_once_with("SL6 3YZ")
            assert result.simon_commute.duration_minutes == 45
