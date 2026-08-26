"""Tests for the FastAPI server endpoints."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from houses.server import app
from houses.settings import settings

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

    def test_rejects_property_already_in_database(self):
        """Re-adding a Rightmove property whose RID already has DAG rows
        in the database must be rejected — the DB is the source of truth
        for duplicates."""
        from dag.persistence import save_node_result
        rid = "88375570"

        save_node_result(
            f"{rid}/rightmove_address",
            {"status": "succeeded", "value": "12 Test Street, Testown RG1 1AA", "succeeded": True},
        )
        resp = client.post(
            "/api/properties",
            json={"url": f"https://www.rightmove.co.uk/properties/{rid}"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text[:100]}"
        body = resp.json()
        assert "already exists" in body.get("error", ""), f"Missing 'already exists' message: {body}"
        assert "fields=" in body.get("error", ""), f"Missing fields= hint: {body}"

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
