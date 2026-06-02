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
        "address": "High Street, Some Town, RG14 1AA",
    }

    def test_valid_payload_returns_success(self):
        resp = client.post("/inject-property", json=self.VALID_PAYLOAD)
        # Accept 200 (sheets not configured) or 201 (written to sheet)
        assert resp.status_code in (200, 201)
        body = resp.json()
        assert body["status"] == "ok"
        assert "data" in body or "row_url" in body

    def test_minimal_payload_with_only_url(self):
        resp = client.post("/inject-property", json={"url": "https://www.rightmove.co.uk/properties/1"})
        assert resp.status_code in (200, 201)
        body = resp.json()
        if "data" in body:
            assert body["data"]["postcode"] == ""

    def test_rejects_non_rightmove_url(self):
        payload = {**self.VALID_PAYLOAD, "url": "https://example.com/"}
        resp = client.post("/inject-property", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "URL must be a Rightmove listing"

    def test_rejects_invalid_types(self):
        payload = {**self.VALID_PAYLOAD, "bedrooms": "three"}
        resp = client.post("/inject-property", json=payload)
        assert resp.status_code == 422

    def test_enrichment_fields_returned(self):
        resp = client.post("/inject-property", json=self.VALID_PAYLOAD)
        body = resp.json()
        # When written to sheet (201), enrichments aren't echoed back
        # When sheets not configured (200), they're in the 'data' field
        if "data" in body:
            data = body["data"]
            assert "simon_commute" in data
            assert "lorena_commute" in data
            assert "petrol" in data
            assert "primary_school" in data
            assert "secondary_school" in data
        else:
            assert "row_url" in body
