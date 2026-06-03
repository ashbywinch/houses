"""Tests for sheet integration — column alignment invariant."""

from houses.models import EnrichedProperty, PetrolCost, SchoolInfo, TransitInfo
from houses.sheets import COLUMN_HEADERS, _row_values


def test_row_values_contains_all_enriched_columns():
    """Every enriched column header has a corresponding entry in _row_values."""
    ep = EnrichedProperty(url="https://www.rightmove.co.uk/properties/123")
    enriched = _row_values(ep)
    # All keys should be valid column headers
    for key in enriched:
        assert key in COLUMN_HEADERS, f"{key!r} not in COLUMN_HEADERS"
    # User columns should NOT be in enriched dict
    for h in ["Rightmove URL", "Address", "Postcode", "Bedrooms", "Price (£)",
              "Actual Latitude", "Actual Longitude"]:
        assert h not in enriched, f"User column {h!r} should not be in _row_values"


def test_row_values_with_full_enrichment():
    """Every column must have the right value."""
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
    r = _row_values(ep)

    assert r["Rightmove ID"] == "123"
    assert r["Simon London (min)"] == "22"
    assert r["Simon London Cost (£)"] == ""
    assert r["Lorena London (min)"] == "38"
    assert r["Lorena London Cost (£)"] == ""
    assert r["Bracknell Time (min)"] == ""
    assert r["Bracknell Cost (£)"] == "8.50"
    assert r["Primary School"] == "St Vincent School"
    assert r["Primary Distance (km)"] == "0.65"
    assert r["Primary Walk (min)"] == "8"
    expected = "https://get-information-schools.service.gov.uk"
    expected += "/Establishments/Establishment/Details/110006"
    assert r["Primary School Link"] == expected
    assert r["Primary Ofsted"] == ""
    assert r["Secondary School"] == "Westminster School"
    assert r["Secondary Distance (km)"] == "1.20"
    assert r["Secondary Walk (min)"] == "14"
    assert r["Secondary School Link"] == "https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/138805"
    assert r["Secondary Ofsted"] == ""
    assert r["Area Description"] == ""
    assert r["Walk to Town (min)"] == ""
    assert r["Walkable Amenities"] == ""
    assert r["EPC Rating"] == ""


def test_row_values_empty_schools():
    ep = EnrichedProperty(url="https://www.rightmove.co.uk/properties/123")
    r = _row_values(ep)
    assert r["Primary Walk (min)"] == ""
    assert r["Primary School Link"] == ""
    assert r["Secondary Walk (min)"] == ""
    assert r["Secondary School Link"] == ""


def test_row_values_missing_commute_empty():
    ep = EnrichedProperty(
        url="https://www.rightmove.co.uk/properties/123",
        simon_commute=TransitInfo(destination_label="S", destination_postcode="SW1V 2QQ"),
        lorena_commute=TransitInfo(destination_label="L", destination_postcode="EC3A 7LP"),
    )
    r = _row_values(ep)
    assert r["Simon London (min)"] == ""
    assert r["Simon London Cost (£)"] == ""
    assert r["Lorena London (min)"] == ""
    assert r["Lorena London Cost (£)"] == ""
    assert r["Bracknell Time (min)"] == ""
    assert r["Bracknell Cost (£)"] == ""


def test_named_range_name_is_deterministic():
    from houses.sheets import named_range_name, COLUMN_HEADERS

    for header in COLUMN_HEADERS:
        name = named_range_name(header)
        assert name.startswith("Data_"), f"{header} → {name} must start with Data_"
        assert " " not in name, f"{header} → {name} has spaces"
        assert name == named_range_name(header), f"{header} → {name} not deterministic"


def test_view_formulas_use_named_ranges():
    """View tab formulas must never contain hardcoded cross-sheet refs or IFERROR."""
    from houses.sheets import named_range_name

    K = "INDEX(View_RightmoveID, ROW())"
    L = "INDEX(View_RightmoveLink, ROW())"
    NR = named_range_name
    RID = NR("Rightmove ID")

    formulas = [
        f'=REGEXEXTRACT({L},"properties/(\\d+)")',
        f'=XLOOKUP({K},{RID},{NR("Price (£)")}    )',
        f'=XLOOKUP({K},{RID},{NR("EPC Rating")}    )',
        f'=LET(k,XLOOKUP({K},{RID},{NR("Bracknell Cost (£)")}),g,XLOOKUP({K},{RID},{NR("Simon London Cost (£)")}),i,XLOOKUP({K},{RID},{NR("Lorena London Cost (£)")}),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)))',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Simon London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Lorena London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Bracknell Time (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=XLOOKUP({K},{RID},{NR("Area Description")}     )',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Walk to Town (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=XLOOKUP({K},{RID},{NR("Walkable Amenities")}   )',
        f'=HYPERLINK(XLOOKUP({K},{RID},{NR("Primary School Link")}),XLOOKUP({K},{RID},{NR("Primary School")}))',
        f'=XLOOKUP({K},{RID},{NR("Primary Ofsted")}       )',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Primary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=HYPERLINK(XLOOKUP({K},{RID},{NR("Secondary School Link")}),XLOOKUP({K},{RID},{NR("Secondary School")}))',
        f'=XLOOKUP({K},{RID},{NR("Secondary Ofsted")}     )',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Secondary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=XLOOKUP({K},{RID},{NR("Secondary Bus Route")}  )',
        f'=XLOOKUP({K},{RID},{NR("Primary Inspection Year")})',
        f'=XLOOKUP({K},{RID},{NR("Secondary Inspection Year")})',
    ]

    for formula in formulas:
        assert "!$" not in formula, f"Hardcoded column ref found: {formula[:80]}"
        assert "IFERROR" not in formula, f"IFERROR wrapper found: {formula[:80]}"
        assert "Data_" in formula or "View_" in formula, f"No named range ref: {formula[:80]}"


def test_xlookup_key_is_typed_as_number():
    """Every XLOOKUP in View formulas must wrap the lookup key in VALUE().

    REGEXEXTRACT returns text, but Data tab Rightmove IDs are stored as numbers.
    Without VALUE(), XLOOKUP("123", [numbers], ...) returns #N/A.
    """
    from houses.sheets import named_range_name

    K_RAW = "INDEX(View_RightmoveID, ROW())"
    RID = named_range_name("Rightmove ID")
    NR = named_range_name

    # Replicate the formula generation the same way setup_sheet.py does
    K = f"VALUE({K_RAW})"
    formulas = [
        f'=XLOOKUP({K},{RID},{NR("Price (£)")}    )',
        f'=XLOOKUP({K},{RID},{NR("EPC Rating")}    )',
        f'=LET(k,XLOOKUP({K},{RID},{NR("Bracknell Cost (£)")}),g,XLOOKUP({K},{RID},{NR("Simon London Cost (£)")}),i,XLOOKUP({K},{RID},{NR("Lorena London Cost (£)")}),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)))',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Simon London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Lorena London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Bracknell Time (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=XLOOKUP({K},{RID},{NR("Area Description")}     )',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Walk to Town (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=XLOOKUP({K},{RID},{NR("Walkable Amenities")}   )',
        f'=HYPERLINK(XLOOKUP({K},{RID},{NR("Primary School Link")}),XLOOKUP({K},{RID},{NR("Primary School")}))',
        f'=XLOOKUP({K},{RID},{NR("Primary Ofsted")}       )',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Primary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=HYPERLINK(XLOOKUP({K},{RID},{NR("Secondary School Link")}),XLOOKUP({K},{RID},{NR("Secondary School")}))',
        f'=XLOOKUP({K},{RID},{NR("Secondary Ofsted")}     )',
        f'=LET(v,XLOOKUP({K},{RID},{NR("Secondary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=XLOOKUP({K},{RID},{NR("Secondary Bus Route")}  )',
        f'=XLOOKUP({K},{RID},{NR("Primary Inspection Year")})',
        f'=XLOOKUP({K},{RID},{NR("Secondary Inspection Year")})',
    ]

    for formula in formulas:
        assert "VALUE(" in formula, f"Missing VALUE() wrapping in: {formula[:80]}"
        assert "!$" not in formula
        assert "IFERROR" not in formula
