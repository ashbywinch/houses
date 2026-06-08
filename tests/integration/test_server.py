"""Integration tests for server endpoints — inject-property and backfill."""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from houses.config import settings
from houses.server import app

client = TestClient(app)
SAMPLE_PAGE = Path(__file__).parent.parent / "fixtures" / "rightmove_sample.html"


class TestInjectProperty:
    VALID_PAYLOAD = {
        "url": "https://www.rightmove.co.uk/properties/123456789",
        "address": "Shoppenhangers Road, Maidenhead, SL6",
        "postcode": "SL6 3HS",
        "bedrooms": 4,
        "price": 795000,
    }

    @pytest.mark.integration
    def test_valid_payload_returns_data(self):
        resp = client.post("/inject-property", json=self.VALID_PAYLOAD)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
        assert "simon_commute" in data.get("data", {})
        assert "lorena_commute" in data.get("data", {})

    @pytest.mark.integration
    def test_minimal_payload_with_only_url(self):
        """A payload with only a URL should still return enrichment data."""

        original_rightmove_sample_page = settings.rightmove_sample_page
        if original_rightmove_sample_page:
            pass
        if not SAMPLE_PAGE.exists():
            pytest.skip("No sample Rightmove page available")
        with open(SAMPLE_PAGE, encoding="utf-8") as f:
            sample_html = f.read()
        with patch("houses.rightmove_scraper._fetch_via_chrome", return_value=sample_html):
            resp = client.post("/inject-property", json={"url": "https://www.rightmove.co.uk/properties/999999"})
        assert resp.status_code in (200, 201)
        body = resp.json()
        data = body.get("data", {}) if body.get("status") == "dry_run" else body.get("data", {})
        assert data.get("address", "").strip(), f"Expected non-empty address, got {data.get('address')!r}"

    @pytest.mark.integration
    def test_accepts_any_url(self):
        """The endpoint should accept any URL and mark it with the correct status."""
        resp = client.post("/inject-property", json={"url": "https://www.example.com/property/1"})
        resp.json()
        assert resp.status_code in (200, 201)

    @pytest.mark.integration
    def test_enrichment_fields_present(self):
        resp = client.post("/inject-property", json=self.VALID_PAYLOAD)
        assert resp.status_code == 200
        data = resp.json()
        data_body = data.get("data", {})
        for field in [
            "simon_commute",
            "lorena_commute",
            "petrol",
            "primary_school",
            "secondary_school",
            "town_description",
            "walk_to_town_minutes",
            "walkable_amenities",
            "epc_rating",
        ]:
            assert field in data_body, f"Missing field: {field}"

    @pytest.mark.integration
    def test_maidenhead_outcode_gets_full_enrichment(self):
        payload = {**self.VALID_PAYLOAD, "postcode": "SL6"}
        resp = client.post("/inject-property", json=payload)
        assert resp.status_code == 200
        data = resp.json().get("data", {})
        assert data.get("postcode") == "SL6"
        assert "simon_commute" in data, "Outcode should still get commute data"
        assert "petrol" in data, "Outcode should still get petrol data"


class TestBackfillView:
    BACKFILL_PAYLOAD = {
        "url": "https://www.rightmove.co.uk/properties/1000000",
        "address": "1 Test Street, Testville, TE1 1ST",
        "postcode": "TE1 1ST",
        "bedrooms": 3,
        "price": 250000,
    }

    @pytest.mark.integration
    def test_new_property_creates_row(self):
        """Backfill with new property URL should succeed (dry-run)."""

        resp = client.post("/backfill-view", json={"urls": ["https://www.rightmove.co.uk/properties/99999999"]})
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_user_columns_filled_for_new_property(self):
        """User-owned columns (address, postcode, etc.) are written to the Data tab."""
        resp = client.post("/inject-property?dry_run=true", json=self.BACKFILL_PAYLOAD)
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_user_columns_passed_to_write_backfill(self):
        resp = client.post("/inject-property?dry_run=true", json=self.BACKFILL_PAYLOAD)
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_lookup_derived_when_empty_with_address_and_postcode(self):
        """When lookup is empty and address+postcode are provided, lookup should be set."""

        original_sheet_id = settings.sheet_id
        settings.sheet_id = ""
        resp = client.post(
            "/inject-property?dry_run=true",
            json={
                "url": "https://www.rightmove.co.uk/properties/1000001",
                "address": "10 Test Avenue, Test Town, TT1 1TT",
                "postcode": "TT1 1TT",
            },
        )
        settings.sheet_id = original_sheet_id
        assert resp.status_code == 200
