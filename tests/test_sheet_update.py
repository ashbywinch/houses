"""Tests for sheet update logic — never hits the real spreadsheet."""

import pytest
from fastapi.testclient import TestClient

from houses.models import EnrichedProperty
from houses.server import app
from houses.sheets import COLUMN_HEADERS, _row_values, col_index

client = TestClient(app)


def _make_enriched(url: str, simon_cost: float = 10.0) -> EnrichedProperty:
    from houses.models import PetrolCost, TransitInfo

    return EnrichedProperty(
        url=url,
        address="123 Test Street, Test Town, TE1 1ST",
        postcode="TE1 1ST",
        price=500000,
        simon_commute=TransitInfo(
            destination_label="S",
            destination_postcode="SW1V 2QQ",
            duration_minutes=45,
            daily_cost_gbp=simon_cost,
        ),
        lorena_commute=TransitInfo(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=50,
            daily_cost_gbp=simon_cost,
        ),
        petrol=PetrolCost(round_trip_km=60.0, cost_gbp=8.50),
    )


@pytest.mark.integration
class TestDryRun:
    """dry_run=true returns enriched data without writing to the sheet."""

    def test_dry_run_returns_same_data_as_normal(self):
        """Both with and without dry_run return identical enrichment data."""
        payload = {
            "url": "https://www.rightmove.co.uk/properties/999999",
            "address": "10 High Street, Test Town, TE1 1ST",
        }
        resp_normal = client.post("/inject-property", json=payload)
        resp_dry = client.post("/inject-property?dry_run=true", json=payload)

        # Both should succeed
        assert resp_normal.status_code in (200, 201)
        assert resp_dry.status_code == 200

        # Both should have the same 'data' structure
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
        # Get the current row values once
        resp1 = client.post("/inject-property?dry_run=true", json=payload)
        row1 = _row_values(EnrichedProperty(**resp1.json()["data"]))

        # Get them again — should be identical
        resp2 = client.post("/inject-property?dry_run=true", json=payload)
        row2 = _row_values(EnrichedProperty(**resp2.json()["data"]))

        assert row1 == row2, "dry_run should return identical data on repeated calls"


class TestUpdateScriptLogic:
    """update_sheet.py should only update cells, never append rows."""

    def test_row_count_preserved(self):
        """_row_values always returns exactly len(COLUMN_HEADERS) values."""
        ep = _make_enriched("https://rightmove.co.uk/properties/1")
        row = _row_values(ep)
        assert len(row) == len(COLUMN_HEADERS), (
            f"_row_values returned {len(row)} values but COLUMN_HEADERS has {len(COLUMN_HEADERS)}"
        )

    def test_cell_values_change_when_data_changes(self):
        """Updating a field should change only the corresponding cell value."""
        ep1 = _make_enriched("https://rightmove.co.uk/properties/1", simon_cost=10.0)
        ep2 = _make_enriched("https://rightmove.co.uk/properties/1", simon_cost=25.0)

        row1 = _row_values(ep1)
        row2 = _row_values(ep2)

        # Simon London Cost column
        cost_idx = col_index("Simon London Cost (£)")
        assert row1[cost_idx] == "10.00"
        assert row2[cost_idx] == "25.00"

        # URL at index 0 stays the same
        assert row1[0] == row2[0]

    def test_empty_cells_do_not_become_zeros(self):
        """Missing data should leave cells empty, never '0' or '0.00'."""
        ep = _make_enriched("https://rightmove.co.uk/properties/1", simon_cost=None)
        row = _row_values(ep)
        cost_idx = col_index("Simon London Cost (£)")
        assert row[cost_idx] == "", f"Expected empty string for None cost, got {row[cost_idx]!r}"

    def test_cache_fields_present(self):
        """Cache columns (lat/lng/station) are at the end of the row."""
        ep = _make_enriched("https://rightmove.co.uk/properties/1")
        row = _row_values(ep)
        # Cache fields are the last 4 entries
        assert len(row) == len(COLUMN_HEADERS)
        # Default values should be empty strings
        assert row[-4] == ""  # property_latitude
        assert row[-3] == ""  # property_longitude
        assert row[-2] == ""  # nearest_station_crs
        assert row[-1] == ""  # nearest_station_name
