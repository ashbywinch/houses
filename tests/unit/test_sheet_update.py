"""Tests for sheet update logic — never hits the real spreadsheet."""

from fastapi.testclient import TestClient

from houses.property import EnrichedProperty
from houses.server import app
from houses.sheets import COLUMN_HEADERS, _build_full_row, row_values

client = TestClient(app)


def _make_enriched(url: str, simon_cost: float = 10.0) -> EnrichedProperty:
    from houses.commute import Commute

    return EnrichedProperty(
        url=url,
        address="123 Test Street, Test Town, TE1 1ST",
        postcode="TE1 1ST",
        price=500000,
        simon_commute=Commute(
            destination_label="S",
            destination_postcode="SW1V 2QQ",
            duration_minutes=45,
            daily_cost_gbp=simon_cost,
        ),
        lorena_commute=Commute(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=50,
            daily_cost_gbp=simon_cost,
        ),
        petrol=Commute(
            destination_label="Bracknell",
            destination_postcode="RG12 8YA",
            daily_cost_gbp=8.50,
            mode="drive",
        ),
    )


class TestUpdateScriptLogic:
    """update_sheet.py should only update cells, never append rows."""

    def test_row_count_preserved(self):
        """_build_full_row always returns exactly len(COLUMN_HEADERS) values."""
        ep = _make_enriched("https://rightmove.co.uk/properties/1")
        row = _build_full_row(ep)
        assert len(row) == len(COLUMN_HEADERS), (
            f"_build_full_row returned {len(row)} values but COLUMN_HEADERS has {len(COLUMN_HEADERS)}"
        )

    def test_cell_values_change_when_data_changes(self):
        """Updating a field should change only the corresponding cell value."""
        ep1 = _make_enriched("https://rightmove.co.uk/properties/1", simon_cost=10.0)
        ep2 = _make_enriched("https://rightmove.co.uk/properties/1", simon_cost=25.0)

        row1 = row_values(ep1)
        row2 = row_values(ep2)

        # Simon London Cost column — accessed by header name, not index
        assert row1["Simon London Cost (£)"] == "10.00"
        assert row2["Simon London Cost (£)"] == "25.00"

        # Rightmove ID stays the same
        assert row1["Rightmove ID"] == row2["Rightmove ID"]

    def test_empty_cells_do_not_become_zeros(self):
        """Missing data should leave cells empty, never '0' or '0.00'."""
        ep = _make_enriched("https://rightmove.co.uk/properties/1", simon_cost=None)
        row = row_values(ep)
        assert row["Simon London Cost (£)"] == "", (
            f"Expected empty string for None cost, got {row['Simon London Cost (£)']!r}"
        )

    def test_cache_fields_present(self):
        """Cache columns are present in _row_values."""
        ep = _make_enriched("https://rightmove.co.uk/properties/1")
        row = row_values(ep)
        assert "Approx Latitude (est)" in row
        assert "Approx Longitude (est)" in row
        assert "Approx Station CRS" in row
        assert "Approx Station Name" in row
        # Default values should be empty strings
        assert row["Approx Latitude (est)"] == ""
        assert row["Approx Longitude (est)"] == ""
        assert row["Approx Station CRS"] == ""
        assert row["Approx Station Name"] == ""
