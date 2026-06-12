"""Tests for the FastAPI server endpoints — pure unit tests, no API calls."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from houses.config import settings
from houses.server import app, extract_postcode

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

    def test_rejects_invalid_types(self):
        payload = {**self.VALID_PAYLOAD, "bedrooms": "three"}
        resp = client.post("/properties", json=payload)
        assert resp.status_code == 422


class TestBackfillView:
    def test_no_sheet_id(self):
        original = settings.sheet_id
        settings.sheet_id = ""
        try:
            resp = client.post("/properties")
            assert resp.status_code == 200
        finally:
            settings.sheet_id = original

    def test_skips_row_without_rightmove_id(self):
        original = settings.sheet_id
        settings.sheet_id = "fake-id"
        try:
            mock_view_ws = MagicMock()
            mock_view_ws.get_all_values.return_value = [["Listing Address", "Rightmove Link", "Rightmove ID"]]
            mock_data_ws = MagicMock()
            mock_data_ws.id = 12345
            mock_sh = MagicMock()

            def _worksheet(name):
                return mock_view_ws if name == "Properties View" else mock_data_ws

            mock_sh.worksheet.side_effect = _worksheet
            mock_client = MagicMock()
            mock_client.open_by_key.return_value = mock_sh
            with patch("houses.server.get_client", return_value=mock_client):
                resp = client.post("/properties")
            assert resp.status_code == 200
        finally:
            settings.sheet_id = original
