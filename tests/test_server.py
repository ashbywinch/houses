"""Tests for the FastAPI server endpoints."""

from fastapi.testclient import TestClient

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

    MAIDENHEAD_PAYLOAD = {
        "url": "https://www.rightmove.co.uk/properties/173431283",
        "address": "Shoppenhangers Road, Maidenhead, SL6",
        "bedrooms": 5,
        "price": 775000,
    }

    def test_valid_payload_returns_data(self):
        resp = client.post("/inject-property", json=self.VALID_PAYLOAD)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "data" in body

    def test_minimal_payload_with_only_url(self):
        resp = client.post("/inject-property", json={"url": "https://www.rightmove.co.uk/properties/1"})
        assert resp.status_code == 200
        assert resp.json()["data"]["postcode"] == ""

    def test_rejects_non_rightmove_url(self):
        payload = {**self.VALID_PAYLOAD, "url": "https://example.com/"}
        resp = client.post("/inject-property", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "URL must be a Rightmove listing"

    def test_rejects_invalid_types(self):
        payload = {**self.VALID_PAYLOAD, "bedrooms": "three"}
        resp = client.post("/inject-property", json=payload)
        assert resp.status_code == 422

    def test_enrichment_fields_present(self):
        resp = client.post("/inject-property", json=self.VALID_PAYLOAD)
        data = resp.json()["data"]
        assert "simon_commute" in data
        assert "lorena_commute" in data
        assert "petrol" in data
        assert "primary_school" in data
        assert "secondary_school" in data

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
