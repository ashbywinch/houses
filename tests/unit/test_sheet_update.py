"""Tests for sheet row-building logic — never hits the real spreadsheet."""

from fastapi.testclient import TestClient

from houses.property import EnrichedProperty
from houses.server import app
from houses.sheets import COLUMN_HEADERS
from houses.sheets.row import Row

client = TestClient(app)


def _make_enriched(url: str, simon_cost: float | None = 10.0) -> EnrichedProperty:
    from money import Money
    from pint import Quantity

    from houses.model.domain import Commute, Person, PlaceOfInterest

    simon_money = Money(str(simon_cost), "GBP") if simon_cost is not None else Money("0", "GBP")
    lorena_money = Money(str(simon_cost), "GBP") if simon_cost is not None else Money("0", "GBP")

    return EnrichedProperty(
        url=url,
        address="123 Test Street, Test Town, TE1 1ST",
        postcode="TE1 1ST",
        price=500000,
        simon_commute=Commute(
            person=Person(name="Simon", has_car=False),
            label="S",
            destination=PlaceOfInterest(label="S", address="SW1V 2QQ"),
            duration=Quantity(45, "minute"),
            daily_cost=simon_money,
            mode="transit",
        ),
        lorena_commute=Commute(
            person=Person(name="Lorena", has_car=False),
            label="L",
            destination=PlaceOfInterest(label="L", address="EC3A 7LP"),
            duration=Quantity(50, "minute"),
            daily_cost=lorena_money,
            mode="transit",
        ),
        petrol=Commute(
            person=Person(name="", has_car=True),
            label="Bracknell",
            destination=PlaceOfInterest(label="Bracknell", address="RG12 8YA"),
            duration=Quantity(0, "minute"),
            daily_cost=Money("8.50", "GBP"),
            mode="drive",
        ),
    )


class TestUpdateScriptLogic:
    """update_sheet.py should produce correctly-sized rows."""

    def test_row_count_preserved(self):
        """Row.to_list always returns exactly len(COLUMN_HEADERS) values."""
        ep = _make_enriched("https://rightmove.co.uk/properties/1")
        row = Row.to_list(ep)
        assert len(row) == len(COLUMN_HEADERS), (
            f"Row.to_list returned {len(row)} values but COLUMN_HEADERS has {len(COLUMN_HEADERS)}"
        )

    def test_cell_values_change_when_data_changes(self):
        """Updating a field should change only the corresponding cell value."""
        ep1 = _make_enriched("https://rightmove.co.uk/properties/1", simon_cost=10.0)
        ep2 = _make_enriched("https://rightmove.co.uk/properties/1", simon_cost=25.0)

        row1 = Row.from_property(ep1)
        row2 = Row.from_property(ep2)

        # Simon London Cost column — accessed by header name, not index
        assert row1["Simon London Cost (£)"] == "10.0"
        assert row2["Simon London Cost (£)"] == "25.0"

        # Rightmove ID stays the same
        assert row1["Rightmove ID"] == row2["Rightmove ID"]
        ep = _make_enriched("https://rightmove.co.uk/properties/1", simon_cost=None)
        row = Row.from_property(ep)
        assert row["Simon London Cost (£)"] == "0", (
            f"Expected '0' for None cost (daily_cost defaults to Money(0)), got {row['Simon London Cost (£)']!r}"
        )

    def test_cache_fields_present(self):
        """Cache columns are present in Row.from_property."""
        ep = _make_enriched("https://rightmove.co.uk/properties/1")
        row = Row.from_property(ep)
        assert "Approx Latitude (est)" in row
        assert "Approx Longitude (est)" in row
        assert "Approx Station CRS" in row
        assert "Approx Station Name" in row
        # Default values should be empty strings
        assert row["Approx Latitude (est)"] == ""
        assert row["Approx Longitude (est)"] == ""
        assert row["Approx Station CRS"] == ""
        assert row["Approx Station Name"] == ""
