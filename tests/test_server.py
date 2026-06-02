"""Tests for the FastAPI server endpoints."""

from fastapi.testclient import TestClient

from houses.server import app

client = TestClient(app)


class TestHealth:
    def test_health_returns_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestInjectProperty:
    VALID_PAYLOAD = {
        "url": "https://www.rightmove.co.uk/properties/123456789",
        "postcode": "SW1A 1AA",
        "bedrooms": 3,
        "price": 650000,
    }

    def test_valid_payload_returns_200_when_sheets_not_configured(self):
        resp = client.post("/inject-property", json=self.VALID_PAYLOAD)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["note"] == "Sheets not configured"
        # Enriched data should be returned
        assert "data" in body
        assert body["data"]["url"] == self.VALID_PAYLOAD["url"]
        assert body["data"]["postcode"] == self.VALID_PAYLOAD["postcode"]

    def test_rejects_non_rightmove_url(self):
        payload = {**self.VALID_PAYLOAD, "url": "https://example.com/"}
        resp = client.post("/inject-property", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "URL must be a Rightmove listing"

    def test_rejects_missing_fields(self):
        resp = client.post("/inject-property", json={"url": "https://www.rightmove.co.uk/properties/1"})
        assert resp.status_code == 422  # FastAPI validation error

    def test_rejects_invalid_types(self):
        payload = {**self.VALID_PAYLOAD, "bedrooms": "three"}
        resp = client.post("/inject-property", json=payload)
        assert resp.status_code == 422

    def test_enrichment_fields_returned(self):
        resp = client.post("/inject-property", json=self.VALID_PAYLOAD)
        body = resp.json()
        data = body["data"]
        # All enrichment fields should be present (null if APIs unavailable)
        assert "simon_commute" in data
        assert "lorena_commute" in data
        assert "petrol" in data
        assert "primary_school" in data
        assert "secondary_school" in data
