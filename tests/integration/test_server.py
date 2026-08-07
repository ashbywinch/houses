"""Tests for the FastAPI server endpoints."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from houses.config import settings
from houses.server import app

client = TestClient(app)


def _inject_session(c: TestClient) -> None:
    """Add a valid signed session cookie — /api/* routes require auth."""
    from houses.web.auth import _make_session_cookie

    c.cookies.set(
        "session",
        _make_session_cookie(
            email="simon@example.com",
            name="Simon",
            picture="",
            is_superuser=True,
        ),
    )


_inject_session(client)


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
        resp = client.post("/api/properties", json=self.VALID_PAYLOAD)
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
            resp = client.post("/api/properties", json={"url": "https://www.rightmove.co.uk/properties/1"})
            assert resp.status_code == 200
            assert "url" in resp.json()["data"]
        finally:
            settings.rightmove_sample_page = original

    @pytest.mark.integration
    def test_accepts_any_url(self):
        payload = {**self.VALID_PAYLOAD, "url": "https://example.com/"}
        resp = client.post("/api/properties", json=payload)
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
                    "/api/properties",
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
        resp = client.post("/api/properties", json=self.VALID_PAYLOAD)
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
        """Address with only outcode 'SL6' — server must accept it."""
        resp = client.post("/api/properties", json=self.MAIDENHEAD_PAYLOAD)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["url"] == self.MAIDENHEAD_PAYLOAD["url"]
        assert data["address"] == self.MAIDENHEAD_PAYLOAD["address"]
        assert data["postcode"] == "SL6"
