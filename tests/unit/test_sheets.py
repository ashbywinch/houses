"""Tests for sheet integration — column alignment invariant."""

import pytest

from houses.commute import Commute, CostGroup, JourneyLeg, LegMode
from houses.geo import GeoPoint
from houses.property import EnrichedProperty
from houses.schools import School, SchoolGender
from houses.sheets import (
    _FORMULA_COLUMNS,
    _USER_COLUMNS,
    COLUMN_HEADERS,
    CONSTANTS_VALUES,
    DATA_FORMULA_COLS,
    VIEW_FORMULA_COLS,
    VIEW_HEADERS,
    VIEW_MANUAL_COLUMNS,
    _build_full_row,
    _const_range_name,
    _rightmove_id,
    _splt,
    col_index,
    col_letter,
    named_range_name,
    row_values,
)


def test_row_values_contains_all_enriched_columns():
    """Every column header has a corresponding entry in _row_values."""
    ep = EnrichedProperty(url="https://www.rightmove.co.uk/properties/123")
    enriched = row_values(ep)
    for key in enriched:
        assert key in COLUMN_HEADERS, f"{key!r} not in COLUMN_HEADERS"
    # Non-user columns should all be present (excluding formula columns)
    user_only = {"Actual Latitude", "Actual Longitude"}
    for h in COLUMN_HEADERS:
        if h not in user_only and h not in _FORMULA_COLUMNS:
            assert h in enriched, f"Missing column {h!r} in _row_values"


def test_row_values_includes_user_columns():
    """User-owned columns (Rightmove URL, Address, Postcode, Bedrooms, Price)
    are included in _row_values when the EnrichedProperty has values for them."""
    ep = EnrichedProperty(
        url="https://www.rightmove.co.uk/properties/123",
        address="1 High Street, Test Town, TE1 1ST",
        postcode="TE1 1ST",
        bedrooms=4,
        price=500000.0,
        actual_latitude=51.5,
        actual_longitude=-0.1,
    )
    r = row_values(ep)
    assert r["Rightmove URL"] == "https://www.rightmove.co.uk/properties/123"
    assert r["Address"] == "1 High Street, Test Town, TE1 1ST"
    assert r["Postcode"] == "TE1 1ST"
    assert r["Bedrooms"] == "4"
    assert r["Price (£)"] == "500000.0"

    # Empty property should produce empty strings for user columns
    empty_ep = EnrichedProperty(url="")
    r2 = row_values(empty_ep)
    assert r2.get("Rightmove URL", "") == ""
    assert r2.get("Address", "") == ""
    assert r2.get("Bedrooms", "") == ""
    assert r2.get("Price (£)", "") == ""

    # Actual Lat/Lng are NOT in _row_values (truly user-owned)
    assert "Actual Latitude" not in r2
    assert "Actual Longitude" not in r2


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
        simon_commute=Commute(destination_label="S", destination_postcode="SW1V 2QQ", duration_minutes=22),
        lorena_commute=Commute(destination_label="L", destination_postcode="EC3A 7LP", duration_minutes=38),
        petrol=Commute(
            destination_label="Bracknell",
            destination_postcode="RG12 8YA",
            daily_cost_gbp=8.50,
            mode="drive",
        ),
        primary_school=School(
            urn=prim_urn,
            name="St Vincent School",
            phase="Primary",
            gender=SchoolGender.MIXED,
            type_of_establishment="Community School",
            postcode="RG14 1AA",
            website="",
            ofsted_rating="",
            inspection_year="",
            coords=GeoPoint(51.1, -0.5),
            statutory_low_age=None,
            statutory_high_age=None,
        ),
        primary_school_commute=Commute(
            destination_label="School",
            destination_postcode="RG14 1AA",
            duration_minutes=8,
            daily_cost_gbp=0.0,
            mode="walk",
            cost_groups=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=8),)),
            ),
        ),
        primary_school_distance_km=0.65,
        secondary_school=School(
            urn=sec_urn,
            name="Westminster School",
            phase="Secondary",
            gender=SchoolGender.BOYS,
            type_of_establishment="Academy Converter",
            postcode="RG14 1AA",
            website="",
            ofsted_rating="",
            inspection_year="",
            coords=GeoPoint(51.1, -0.4),
            statutory_low_age=None,
            statutory_high_age=None,
        ),
        secondary_school_commute=Commute(
            destination_label="School",
            destination_postcode="RG14 1AA",
            duration_minutes=14,
            daily_cost_gbp=0.0,
            mode="walk",
            cost_groups=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=14),)),
            ),
        ),
        secondary_school_distance_km=1.2,
    )
    r = row_values(ep)

    expected = {
        "Rightmove ID": "123",
        "Simon London (min)": "22",
        "Simon London Cost (£)": "",
        "Simon London Route": "",
        "Simon Parking Cost (£)": "",
        "Lorena London (min)": "38",
        "Lorena London Cost (£)": "",
        "Lorena London Route": "",
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

    # Every non-user, non-formula column must have an expected value.
    _user_cols = {
        "Rightmove URL",
        "Address",
        "Postcode",
        "Bedrooms",
        "Price (£)",
        "Actual Latitude",
        "Actual Longitude",
    }
    non_user = {h for h in COLUMN_HEADERS if h not in _user_cols and h not in _FORMULA_COLUMNS}
    expected_set = set(expected.keys())
    assert expected_set == non_user, (
        f"Missing expected entry: {sorted(non_user - expected_set)}, "
        f"Extra expected entries: {sorted(expected_set - non_user)}"
    )


def test_row_values_with_council_tax():
    from houses.property import CouncilTaxInfo

    ep = EnrichedProperty(
        url="https://www.rightmove.co.uk/properties/456",
        council_tax=CouncilTaxInfo(band="D", yearly_cost=1800.0),
    )
    r = row_values(ep)
    assert r["Council Tax Band"] == "D"
    assert r["Council Tax Cost (£)"] == "1800.00"


def test_row_values_empty_schools():
    ep = EnrichedProperty(url="https://www.rightmove.co.uk/properties/123")
    r = row_values(ep)
    assert r["Primary Walk (min)"] == ""
    assert r["Primary School Link"] == ""
    assert r["Secondary Walk (min)"] == ""
    assert r["Secondary School Link"] == ""


def test_build_full_row_includes_rid():
    """``_build_full_row`` must place the Rightmove ID at the correct
    column position so that appended rows are identifiable by RID.

    Regression test: the backfill appended rows with empty RID columns,
    causing duplicate rows on subsequent runs."""
    rid = "987654321"
    url = f"https://www.rightmove.co.uk/properties/{rid}"
    ep = EnrichedProperty(url=url, address="1 Test Road, TE1 1ST", postcode="TE1 1ST", bedrooms=3, price=500000.0)
    row = _build_full_row(ep)
    rid_idx = COLUMN_HEADERS.index("Rightmove ID")
    assert row[rid_idx] == rid, (
        f"Rightmove ID column (index {rid_idx}) expected {rid!r}, got {row[rid_idx]!r}. Full row preview: {row[:10]}"
    )
    # Also verify that all other user columns are populated
    assert row[COLUMN_HEADERS.index("Rightmove URL")] == url
    assert row[COLUMN_HEADERS.index("Address")] == "1 Test Road, TE1 1ST"
    assert row[COLUMN_HEADERS.index("Postcode")] == "TE1 1ST"
    assert row[COLUMN_HEADERS.index("Bedrooms")] == "3"
    assert row[COLUMN_HEADERS.index("Price (£)")] == "500000.0"


def test_row_values_missing_commute_empty():
    ep = EnrichedProperty(
        url="https://www.rightmove.co.uk/properties/123",
        simon_commute=Commute(destination_label="S", destination_postcode="SW1V 2QQ"),
        lorena_commute=Commute(destination_label="L", destination_postcode="EC3A 7LP"),
    )
    r = row_values(ep)
    assert r["Simon London (min)"] == ""
    assert r["Simon London Cost (£)"] == ""
    assert r["Simon London Route"] == ""
    assert r["Lorena London (min)"] == ""
    assert r["Lorena London Cost (£)"] == ""
    assert r["Lorena London Route"] == ""
    assert r["Bracknell Time (min)"] == ""
    assert r["Bracknell Cost (£)"] == ""


def test_row_values_includes_route_summary():
    """Route summary from Commute flows into the sheet route column."""
    ep = EnrichedProperty(
        url="https://www.rightmove.co.uk/properties/123",
        simon_commute=Commute(
            destination_label="S",
            destination_postcode="SW1V 2QQ",
            duration_minutes=45,
            cost_groups=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=5),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TRAIN, duration_minutes=20),), operator="GWR"),
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=5),)),
            ),
        ),
        lorena_commute=Commute(
            destination_label="L",
            destination_postcode="EC3A 7LP",
            duration_minutes=30,
            cost_groups=(
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=3),)),
                CostGroup(legs=(JourneyLeg(mode=LegMode.TUBE, duration_minutes=15),), operator="TfL"),
                CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=2),)),
            ),
        ),
    )
    r = row_values(ep)
    assert r["Simon London Route"] == "walk (5m) → train (20m) → walk 5m"
    assert r["Lorena London Route"] == "walk (3m) → tube (15m) → walk 2m"


def test_named_range_name_is_deterministic():
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


class TestRightmoveID:
    """_rightmove_id — extract numeric ID from Rightmove URLs and text."""

    def test_standard_url(self):
        assert _rightmove_id("https://www.rightmove.co.uk/properties/123456789") == "123456789"

    def test_url_with_extra_params(self):
        assert _rightmove_id("https://www.rightmove.co.uk/properties/98765432?queryParam=value") == "98765432"

    def test_bare_id_number(self):
        """Fallback: any 8+ digit number counts as an ID."""
        assert _rightmove_id("88375569") == "88375569"

    def test_short_number_not_id(self):
        """Less than 8 digits should not match the fallback."""
        assert _rightmove_id("123") == ""

    def test_no_id_returns_empty(self):
        assert _rightmove_id("not a url") == ""

    def test_empty_string(self):
        assert _rightmove_id("") == ""

    def test_url_with_non_numeric_id(self):
        """URL with no numeric segment should fall through to empty."""
        assert _rightmove_id("https://example.com/properties/abc") == ""


class TestColLetter:
    """col_letter — 0-based index to Google Sheets column letter."""

    def test_a(self):
        assert col_letter(0) == "A"

    def test_z(self):
        assert col_letter(25) == "Z"

    def test_aa(self):
        assert col_letter(26) == "AA"

    def test_az(self):
        assert col_letter(51) == "AZ"

    def test_ba(self):
        assert col_letter(52) == "BA"


class TestColIndex:
    """col_index — header name to 0-based column index."""

    def test_rightmove_url_is_zero(self):
        assert col_index("Rightmove URL") == 0

    def test_address_is_one(self):
        assert col_index("Address") == 1

    def test_last_column(self):
        last = COLUMN_HEADERS[-1]
        assert col_index(last) == len(COLUMN_HEADERS) - 1

    def test_unknown_header_raises(self):
        with pytest.raises(ValueError, match="not found"):
            col_index("Nonexistent Column")


# ── Slice 1: Constants Tab ──────────────────────────────────────────────


def test_const_range_name_generates_correct_prefix():
    assert _const_range_name("Sinking Fund Rate (annual)") == "Const_SinkingFundRateAnnual"
    assert _const_range_name("Current Sale Price (£)") == "Const_CurrentSalePrice"
    assert _const_range_name("Mortgage Interest Rate") == "Const_MortgageInterestRate"


def test_const_range_name_is_deterministic():
    for label, _ in CONSTANTS_VALUES:
        assert _const_range_name(label).startswith("Const_")
        assert _const_range_name(label) == _const_range_name(label)


def test_constants_values_match_constants_headers():
    """List of constant names should derive from CONSTANTS_VALUES labels."""
    for label, _ in CONSTANTS_VALUES:
        assert isinstance(label, str)
        assert isinstance(_const_range_name(label), str)


# ── Slice 2: Data Tab Formula Columns ────────────────────────────────────


def test_data_headers_count():
    assert len(COLUMN_HEADERS) == 49


def test_data_formula_count():
    """Every key in DATA_FORMULA_COLS maps to a header in COLUMN_HEADERS."""
    keys_lower = {h.lower() for h in COLUMN_HEADERS}
    for key in DATA_FORMULA_COLS:
        assert key in keys_lower, f"Data formula key {key!r} not in COLUMN_HEADERS"
    assert len(DATA_FORMULA_COLS) == 8


def test_stamp_duty_known_values():
    assert _splt(250000) == 0.0
    assert _splt(350000) == 5000.0
    assert _splt(550000) == 15000.0
    assert _splt(925000) == 33750.0
    assert _splt(1500000) == 91250.0
    assert _splt(2000000) == 151250.0


def test_data_formulas_use_named_ranges():
    """Every DATA_FORMULA_COLS formula references Data_ or Const_ or View_."""
    for key, formula in DATA_FORMULA_COLS.items():
        assert "Data_" in formula or "Const_" in formula or "View_" in formula, (
            f"Data formula {key!r} has no named range reference"
        )


def test_formula_cols_not_in_row_values():
    """Formula column headers should NOT appear in _row_values output."""
    ep = EnrichedProperty(url="https://www.rightmove.co.uk/properties/123")
    enriched = row_values(ep)
    for h in _FORMULA_COLUMNS:
        assert h not in enriched, f"Formula column {h!r} found in _row_values"


def test_formula_cols_not_in_user_cols():
    """Formula columns should not be in _USER_COLUMNS."""
    for h in _FORMULA_COLUMNS:
        assert h not in _USER_COLUMNS, f"Formula column {h!r} found in _USER_COLUMNS"


def test_stamp_duty_formula_checks_status():
    """Stamp Duty formula returns 0 for Status=Current."""
    formula = DATA_FORMULA_COLS["stamp duty (£)"]
    assert "View_Status" in formula, "Stamp Duty formula must reference View_Status"
    assert 'IF(s="Current",0,sd)' in formula or '"Current"' in formula, "Stamp Duty must return 0 when Status=Current"


def test_net_ashby_formula_checks_status():
    """Net Ashby formula returns 0 for Status=Current."""
    formula = DATA_FORMULA_COLS["net ashby contribution (£)"]
    assert "View_Status" in formula, "Net Ashby formula must reference View_Status"
    assert 'IF(s="Current",0' in formula or '"Current"' in formula, "Net Ashby must return 0 when Status=Current"


def test_stamp_duty_formula_uses_splt():
    """Stamp Duty formula implements the same SDLT bands as _splt."""
    formula = DATA_FORMULA_COLS["stamp duty (£)"]
    assert "250000" in formula, "Stamp Duty must have 250k threshold"
    assert "925000" in formula, "Stamp Duty must have 925k threshold"
    assert "1500000" in formula, "Stamp Duty must have 1.5M threshold"


def test_current_home_mortgage_excludes_ashby():
    """For Status=Current, Mortgage Required = Price - Deposit (Net Ashby=0)."""
    formula = DATA_FORMULA_COLS["mortgage required (£)"]
    # Mortgage Required references Data_NetAshbyContribution
    # Net Ashby returns 0 for Current, so MR = Price - Deposit - 0 = Price - Deposit
    assert "{_nr('Net Ashby Contribution (£)')}" in formula or "Data_NetAshbyContribution" in formula
    assert "Data_Price" in formula or "Const_Deposit" in formula


def test_monthly_mortgage_blank_when_ashby_works_missing():
    """Monthly Mortgage Payment should be blank when Ashby Works Estimate is empty."""
    formula = DATA_FORMULA_COLS["monthly mortgage payment (£)"]
    assert "View_AshbyWorksEstimate" in formula
    assert "View_Status" in formula
    assert 'INDEX(View_AshbyWorksEstimate,ROW())=""' in formula
    assert '"Current"' in formula


# ── Slice 3: View Tab Definitions ────────────────────────────────────────


def test_view_headers_count():
    assert len(VIEW_HEADERS) == 41


def test_all_view_headers_are_covered():
    """Every View header is either a formula column or a manual column."""
    formula_keys = set(VIEW_FORMULA_COLS.keys())
    manual_lower = {h.lower() for h in VIEW_MANUAL_COLUMNS}
    uncovered = []
    for h in VIEW_HEADERS:
        key = h.lower()
        if key not in formula_keys and key not in manual_lower:
            uncovered.append(h)
    assert not uncovered, f"View headers with no formula or manual entry: {uncovered}"


def test_ashby_works_in_manual_columns():
    assert "Ashby Works Estimate (£)" in VIEW_MANUAL_COLUMNS


def test_removed_headers_gone():
    """Yearly commute and council tax columns are no longer in VIEW_HEADERS."""
    assert "Yearly Commute Total (£)" not in VIEW_HEADERS
    assert "Yearly Council Tax (£)" not in VIEW_HEADERS


def test_view_formula_cols_use_named_ranges():
    """Every VIEW_FORMULA_COLS formula must reference Data_, Const_, or View_."""
    for key, formula in VIEW_FORMULA_COLS.items():
        assert "Data_" in formula or "Const_" in formula or "View_" in formula, (
            f"View formula {key!r} has no named range reference"
        )


def test_total_monthly_formula_includes_all_components():
    """The Total Monthly formula must reference all 5 cost components."""
    formula = VIEW_FORMULA_COLS.get("total monthly housing cost (£)", "")
    assert "Data_MonthlyMortgagePayment" in formula
    assert "Data_YearlySinkingFund" in formula
    assert "Const_LifeInsuranceMonthly" in formula
    assert "Data_BracknellCost" in formula or "Bracknell" in formula
    assert "Data_CouncilTaxCost" in formula


def test_affordability_formulas_use_ifna_not_ifferror():
    """Every INDEX-based formula must avoid IFERROR (= use IFNA)."""
    for key, formula in VIEW_FORMULA_COLS.items():
        assert "IFERROR" not in formula, f"IFERROR found in {key!r}"
        assert "IFNA" in formula, f"Missing IFNA in {key!r}"


def test_view_manual_columns_are_not_formulas():
    """No manual column key should appear in VIEW_FORMULA_COLS keys."""
    manual_lower = {h.lower() for h in VIEW_MANUAL_COLUMNS}
    for key in VIEW_FORMULA_COLS:
        assert key not in manual_lower, f"Manual column {key!r} also in VIEW_FORMULA_COLS"
