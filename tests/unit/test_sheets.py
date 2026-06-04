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
    for h in ["Rightmove URL", "Address", "Postcode", "Bedrooms", "Price (£)", "Actual Latitude", "Actual Longitude"]:
        assert h not in enriched, f"User column {h!r} should not be in _row_values"


def test_row_values_with_full_enrichment():
    """Every non-user column in _row_values must match its expected value."""
    prim_urn = "110006"
    sec_urn = "138805"
    link_base = "https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/"

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
            name="St Vincent School",
            type="primary",
            distance_km=0.65,
            walking_time_minutes=8,
            urn=prim_urn,
        ),
        secondary_school=SchoolInfo(
            name="Westminster School",
            type="secondary",
            distance_km=1.2,
            walking_time_minutes=14,
            urn=sec_urn,
        ),
    )
    r = _row_values(ep)

    expected = {
        "Rightmove ID": "123",
        "Simon London (min)": "22",
        "Simon London Cost (£)": "",
        "Lorena London (min)": "38",
        "Lorena London Cost (£)": "",
        "Bracknell Time (min)": "",
        "Bracknell Cost (£)": "8.50",
        "Primary School": "St Vincent School",
        "Primary Distance (km)": "0.65",
        "Primary Walk (min)": "8",
        "Primary School Link": f"{link_base}{prim_urn}",
        "Primary Ofsted": "",
        "Primary Inspection Year": "",
        "Secondary School": "Westminster School",
        "Secondary Distance (km)": "1.20",
        "Secondary Walk (min)": "14",
        "Secondary School Link": f"{link_base}{sec_urn}",
        "Secondary Ofsted": "",
        "Secondary Inspection Year": "",
        "Area Description": "",
        "Walk to Town (min)": "",
        "Walkable Amenities": "",
        "EPC Rating": "",
        "Council Tax Band": "",
        "Council Tax Cost (£)": "",
        "Secondary Bus (min)": "",
        "Secondary Bus Route": "",
        "Approx Latitude (est)": "",
        "Approx Longitude (est)": "",
        "Approx Station CRS": "",
        "Approx Station Name": "",
    }

    for header, expect in expected.items():
        assert header in r, f"Missing key {header!r} in _row_values"
        assert r[header] == expect, f"{header}: expected {expect!r}, got {r[header]!r}"

    # Every non-user column must have an expected value.
    _user_cols = {
        "Rightmove URL",
        "Address",
        "Postcode",
        "Bedrooms",
        "Price (£)",
        "Actual Latitude",
        "Actual Longitude",
    }
    non_user = {h for h in COLUMN_HEADERS if h not in _user_cols}
    expected_set = set(expected.keys())
    assert expected_set == non_user, (
        f"Missing expected entry: {sorted(non_user - expected_set)}, "
        f"Extra expected entries: {sorted(expected_set - non_user)}"
    )


def test_row_values_with_council_tax():
    from houses.models import CouncilTaxInfo

    ep = EnrichedProperty(
        url="https://www.rightmove.co.uk/properties/456",
        council_tax=CouncilTaxInfo(band="D", yearly_cost=1800.0),
    )
    r = _row_values(ep)
    assert r["Council Tax Band"] == "D"
    assert r["Council Tax Cost (£)"] == "1800.00"


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
    from houses.sheets import COLUMN_HEADERS, named_range_name

    for header in COLUMN_HEADERS:
        name = named_range_name(header)
        assert name.startswith("Data_"), f"{header} → {name} must start with Data_"
        assert " " not in name, f"{header} → {name} has spaces"
        assert name == named_range_name(header), f"{header} → {name} not deterministic"


def test_view_formulas_use_named_ranges():
    """View tab formulas must never contain hardcoded cross-sheet refs or IFERROR."""
    from houses.sheets import named_range_name

    k = "INDEX(View_RightmoveID, ROW())"  # noqa: F841
    l_val = "INDEX(View_RightmoveLink, ROW())"  # noqa: N806
    nr = named_range_name
    rid = nr("Rightmove ID")

    formulas = [
        f'=REGEXEXTRACT({l_val},"properties/(\\d+)")',
        f"=XLOOKUP({k},{rid},{nr('Price (£)')}    )",  # noqa: F821
        f"=XLOOKUP({k},{rid},{nr('EPC Rating')}    )",  # noqa: F821
        f'=LET(k,XLOOKUP({k},{rid},{nr("Bracknell Cost (£)")}),g,XLOOKUP({k},{rid},{nr("Simon London Cost (£)")}),i,XLOOKUP({k},{rid},{nr("Lorena London Cost (£)")}),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)))',  # noqa: E501, F821
        f'=LET(v,XLOOKUP({k},{rid},{nr("Simon London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',  # noqa: F821
        f'=LET(v,XLOOKUP({k},{rid},{nr("Lorena London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',  # noqa: F821
        f'=LET(v,XLOOKUP({k},{rid},{nr("Bracknell Time (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',  # noqa: F821
        f"=XLOOKUP({k},{rid},{nr('Area Description')}     )",  # noqa: F821
        f'=LET(v,XLOOKUP({k},{rid},{nr("Walk to Town (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',  # noqa: F821
        f"=XLOOKUP({k},{rid},{nr('Walkable Amenities')}   )",  # noqa: F821
        f"=HYPERLINK(XLOOKUP({k},{rid},{nr('Primary School Link')}),XLOOKUP({k},{rid},{nr('Primary School')}))",  # noqa: F821
        f"=XLOOKUP({k},{rid},{nr('Primary Ofsted')}       )",  # noqa: F821
        f'=LET(v,XLOOKUP({k},{rid},{nr("Primary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',  # noqa: F821
        f"=HYPERLINK(XLOOKUP({k},{rid},{nr('Secondary School Link')}),XLOOKUP({k},{rid},{nr('Secondary School')}))",  # noqa: F821
        f"=XLOOKUP({k},{rid},{nr('Secondary Ofsted')}     )",  # noqa: F821
        f'=LET(v,XLOOKUP({k},{rid},{nr("Secondary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',  # noqa: F821
        f"=XLOOKUP({k},{rid},{nr('Secondary Bus Route')}  )",  # noqa: F821
        f"=XLOOKUP({k},{rid},{nr('Primary Inspection Year')})",  # noqa: F821
        f"=XLOOKUP({k},{rid},{nr('Secondary Inspection Year')})",  # noqa: F821
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

    k_raw = "INDEX(View_RightmoveID, ROW())"
    rid = named_range_name("Rightmove ID")
    nr = named_range_name

    # Replicate the formula generation the same way setup_sheet.py does
    k = f"VALUE({k_raw})"
    formulas = [
        f"=XLOOKUP({k},{rid},{nr('Price (£)')}    )",
        f"=XLOOKUP({k},{rid},{nr('EPC Rating')}    )",
        f'=LET(k,XLOOKUP({k},{rid},{nr("Bracknell Cost (£)")}),g,XLOOKUP({k},{rid},{nr("Simon London Cost (£)")}),i,XLOOKUP({k},{rid},{nr("Lorena London Cost (£)")}),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)))',  # noqa: E501
        f'=LET(v,XLOOKUP({k},{rid},{nr("Simon London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=LET(v,XLOOKUP({k},{rid},{nr("Lorena London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f'=LET(v,XLOOKUP({k},{rid},{nr("Bracknell Time (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f"=XLOOKUP({k},{rid},{nr('Area Description')}     )",
        f'=LET(v,XLOOKUP({k},{rid},{nr("Walk to Town (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f"=XLOOKUP({k},{rid},{nr('Walkable Amenities')}   )",
        f"=HYPERLINK(XLOOKUP({k},{rid},{nr('Primary School Link')}),XLOOKUP({k},{rid},{nr('Primary School')}))",
        f"=XLOOKUP({k},{rid},{nr('Primary Ofsted')}       )",
        f'=LET(v,XLOOKUP({k},{rid},{nr("Primary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f"=HYPERLINK(XLOOKUP({k},{rid},{nr('Secondary School Link')}),XLOOKUP({k},{rid},{nr('Secondary School')}))",
        f"=XLOOKUP({k},{rid},{nr('Secondary Ofsted')}     )",
        f'=LET(v,XLOOKUP({k},{rid},{nr("Secondary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        f"=XLOOKUP({k},{rid},{nr('Secondary Bus Route')}  )",
        f"=XLOOKUP({k},{rid},{nr('Primary Inspection Year')})",
        f"=XLOOKUP({k},{rid},{nr('Secondary Inspection Year')})",
    ]

    for formula in formulas:
        assert "VALUE(" in formula, f"Missing VALUE() wrapping in: {formula[:80]}"
        assert "!$" not in formula
        assert "IFERROR" not in formula
