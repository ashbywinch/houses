"""Tests for sheet integration — column alignment invariant."""

from houses.models import EnrichedProperty, PetrolCost, SchoolInfo, TransitInfo
from houses.sheets import COLUMN_HEADERS, _row_values


def test_row_values_matches_header_count():
    ep = EnrichedProperty(url="https://www.rightmove.co.uk/properties/123")
    row = _row_values(ep)
    assert len(row) == len(COLUMN_HEADERS), (
        f"_row_values returned {len(row)} values but COLUMN_HEADERS "
        f"has {len(COLUMN_HEADERS)} columns."
    )


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

    assert row[0] == "https://www.rightmove.co.uk/properties/123"
    assert row[1] == "High Street, Some Town, RG14 1AA"
    assert row[2] == "RG14 1AA"
    assert row[3] == "3"
    assert row[4] == "650,000"
    assert row[5] == "22"
    assert row[6] == "38"
    assert row[7] == "8.50"
    assert row[8] == "St Vincent School"
    assert row[9] == "0.65"
    assert row[10] == "8"                                      # walk min
    assert row[11] == "https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/110006"
    assert row[12] == "Westminster School"
    assert row[13] == "1.20"
    assert row[14] == "14"                                     # walk min
    assert row[15] == "https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/138805"


def test_row_values_empty_schools():
    """When no schools found, walk and link columns stay empty."""
    ep = EnrichedProperty(url="https://www.rightmove.co.uk/properties/123")
    row = _row_values(ep)
    assert row[10] == ""  # primary walk
    assert row[11] == ""  # primary link
    assert row[14] == ""  # secondary walk
    assert row[15] == ""  # secondary link
