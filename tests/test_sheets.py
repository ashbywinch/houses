"""Tests for sheet integration — column alignment invariant."""

from houses.models import EnrichedProperty, PetrolCost, SchoolInfo, TransitInfo
from houses.sheets import COLUMN_HEADERS, _row_values


def test_row_values_matches_header_count():
    """_row_values must produce exactly one value per column header.
    If this fails after changing either COLUMN_HEADERS or _row_values,
    it means data would shift columns when written to the sheet.
    """
    ep = EnrichedProperty(url="https://www.rightmove.co.uk/properties/123")
    row = _row_values(ep)
    assert len(row) == len(COLUMN_HEADERS), (
        f"_row_values returned {len(row)} values but COLUMN_HEADERS "
        f"has {len(COLUMN_HEADERS)} columns. Every column must have a value."
    )


def test_row_values_with_full_enrichment():
    """With all enrichment fields populated, each value should be in the right spot."""
    ep = EnrichedProperty(
        url="https://www.rightmove.co.uk/properties/123",
        address="High Street, Some Town, RG14 1AA",
        postcode="RG14 1AA",
        bedrooms=3,
        price=650000.0,
        simon_commute=TransitInfo(
            destination_label="Simon",
            destination_postcode="SW1V 2QQ",
            duration_minutes=22,
        ),
        lorena_commute=TransitInfo(
            destination_label="Lorena",
            destination_postcode="EC3A 7LP",
            duration_minutes=38,
        ),
        petrol=PetrolCost(round_trip_km=100.0, cost_gbp=8.50),
        primary_school=SchoolInfo(name="St Vincent School", type="primary", distance_km=0.65),
        secondary_school=SchoolInfo(name="Westminster School", type="secondary", distance_km=1.2),
    )
    row = _row_values(ep)

    # Index positions must match COLUMN_HEADERS order
    assert row[0] == "https://www.rightmove.co.uk/properties/123"  # URL
    assert row[1] == "High Street, Some Town, RG14 1AA"  # Address
    assert row[2] == "RG14 1AA"  # Postcode
    assert row[3] == "3"  # Bedrooms
    assert row[4] == "650,000"  # Price
    assert row[5] == "22"  # Simon
    assert row[6] == "38"  # Lorena
    assert row[7] == "8.50"  # Petrol
    assert row[8] == "St Vincent School"  # Primary
    assert row[9] == "0.65"  # Primary dist
    assert row[10] == "Westminster School"  # Secondary
    assert row[11] == "1.20"  # Secondary dist
