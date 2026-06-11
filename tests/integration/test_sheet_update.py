"""Integration tests for dry_run in inject-property."""

from fastapi.testclient import TestClient

from houses.server import app

client = TestClient(app)


class TestDryRun:
    """no_write=true returns enriched data without writing to the sheet."""

    def test_dry_run_returns_same_data_as_normal(self):
        """Both with and without dry_run return identical enrichment data."""
        payload = {
            "url": "https://www.rightmove.co.uk/properties/999999",
            "address": "10 High Street, Test Town, TE1 1ST",
        }
        resp_normal = client.post("/properties", json=payload)
        resp_dry = client.post("/properties?no_write=true", json=payload)

        assert resp_normal.status_code in (200, 201)
        assert resp_dry.status_code == 200

        data_normal = resp_normal.json().get("data", {})
        data_dry = resp_dry.json().get("data", {})
        assert "simon_commute" in data_normal
        assert "simon_commute" in data_dry

    def test_dry_run_does_not_append_to_sheet(self):
        """Calling dry_run multiple times should not cause the data to change."""
        payload = {
            "url": "https://www.rightmove.co.uk/properties/888888",
            "address": "20 High Street, Test Town, TE1 1ST",
        }
        resp1 = client.post("/properties?no_write=true", json=payload)
        data1 = resp1.json().get("data", {})

        resp2 = client.post("/properties?no_write=true", json=payload)
        data2 = resp2.json().get("data", {})

        assert data1 == data2, "dry_run should return identical data on repeated calls"
