"""Tests for sheet integration — column alignment invariant."""

from houses.models import EnrichedProperty, PetrolCost, SchoolInfo, TransitInfo
from houses.sheets import COLUMN_HEADERS, _row_values, col_index


def _col(name):
    return col_index(name)


def test_row_values_matches_header_count():
    ep = EnrichedProperty(url="https://www.rightmove.co.uk/properties/123")
    row = _row_values(ep)
    assert len(row) == len(COLUMN_HEADERS)


def test_row_values_with_full_enrichment():
    """Every column must have the right value in the right position."""
    ep = EnrichedProperty(
        url="https://www.rightmove.co.uk/properties/123",
        address="High Street, Some Town, RG14 1AA",
        postcode="RG14 1AA",
        bedrooms=3,
        price=650000.0,
        simon_commute=TransitInfo(destination_label="S", destination_postcode="SW1V 2QQ", duration_minutes=22),
        lorena_commute=TransitInfo(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=38),
        petrol=PetrolCost(round_trip_km=100.0, cost_gbp=8.50),
        primary_school=SchoolInfo(
            name="St Vincent School", type="primary", distance_km=0.65,
            walking_time_minutes=8, urn="110006",
        ),
        secondary_school=SchoolInfo(
            name="Westminster School", type="secondary", distance_km=1.2,
            walking_time_minutes=14, urn="138805",
        ),
    )
    row = _row_values(ep)

    # User columns (A-G) are never written by the server
    assert row[_col("Rightmove URL")] == ""
    assert row[_col("Address")] == ""
    assert row[_col("Postcode")] == ""
    assert row[_col("Bedrooms")] == ""
    assert row[_col("Price (£)")] == ""
    # Enriched columns start here
    assert row[_col("Rightmove ID")] == "123"
    assert row[_col("Simon London (min)")] == "22"
    assert row[_col("Simon London Cost (£)")] == ""
    assert row[_col("Lorena London (min)")] == "38"
    assert row[_col("Lorena London Cost (£)")] == ""
    assert row[_col("Bracknell Time (min)")] == ""
    assert row[_col("Bracknell Cost (£)")] == "8.50"
    assert row[_col("Primary School")] == "St Vincent School"
    assert row[_col("Primary Distance (km)")] == "0.65"
    assert row[_col("Primary Walk (min)")] == "8"
    expected = "https://get-information-schools.service.gov.uk"
    expected += "/Establishments/Establishment/Details/110006"
    assert row[_col("Primary School Link")] == expected
    assert row[_col("Primary Ofsted")] == ""
    assert row[_col("Secondary School")] == "Westminster School"
    assert row[_col("Secondary Distance (km)")] == "1.20"
    assert row[_col("Secondary Walk (min)")] == "14"
    assert row[_col("Secondary School Link")] == "https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/138805"
    assert row[_col("Secondary Ofsted")] == ""
    assert row[_col("Area Description")] == ""
    assert row[_col("Walk to Town (min)")] == ""
    assert row[_col("Walkable Amenities")] == ""
    assert row[_col("EPC Rating")] == ""


def test_row_values_empty_schools():
    ep = EnrichedProperty(url="https://www.rightmove.co.uk/properties/123")
    row = _row_values(ep)
    assert row[_col("Primary Walk (min)")] == ""
    assert row[_col("Primary School Link")] == ""
    assert row[_col("Secondary Walk (min)")] == ""
    assert row[_col("Secondary School Link")] == ""


def test_row_values_missing_commute_empty():
    ep = EnrichedProperty(
        url="https://www.rightmove.co.uk/properties/123",
        simon_commute=TransitInfo(destination_label="S", destination_postcode="SW1V 2QQ"),
        lorena_commute=TransitInfo(destination_label="L", destination_postcode="EC3A 7LP"),
    )
    row = _row_values(ep)
    assert row[_col("Simon London (min)")] == ""
    assert row[_col("Simon London Cost (£)")] == ""
    assert row[_col("Lorena London (min)")] == ""
    assert row[_col("Lorena London Cost (£)")] == ""
    assert row[_col("Bracknell Time (min)")] == ""
    assert row[_col("Bracknell Cost (£)")] == ""
