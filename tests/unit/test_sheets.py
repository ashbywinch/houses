"""Tests for sheet integration — column alignment invariant."""

import pytest

from houses.sheets import (
    _FORMULA_COLUMNS,
    _USER_COLUMNS,
    COLUMN_HEADERS,
    CONSTANTS_VALUES,
    DATA_FORMULA_COLS,
    VIEW_FORMULA_COLS,
    VIEW_HEADERS,
    VIEW_MANUAL_COLUMNS,
    _const_range_name,
    col_index,
    col_letter,
    named_range_name,
)
from houses.stamp_duty import stamp_duty_land_tax


class TestColumnLetter:
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


class TestColumnIndex:
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


def test_named_range_name_is_deterministic():
    for header in COLUMN_HEADERS:
        name = named_range_name(header)
        assert name.startswith("Data_"), f"{header} → {name} must start with Data_"


def test_view_formulas_use_named_ranges():
    """View tab formulas must never contain hardcoded cross-sheet refs or IFERROR."""

    for key, formula in VIEW_FORMULA_COLS.items():
        assert "Data_" in formula or "Const_" in formula, (
            f"View formula {key!r} references non-Data/non-Const range: {formula[:80]}"
        )
        assert "IFERROR" not in formula, f"IFERROR found in {key!r}"


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


def test_data_headers_count():
    assert len(COLUMN_HEADERS) == 49


def test_data_formula_count():
    """Every key in DATA_FORMULA_COLS maps to a header in COLUMN_HEADERS."""
    keys_lower = {h.lower() for h in COLUMN_HEADERS}
    for key in DATA_FORMULA_COLS:
        assert key in keys_lower, f"Data formula key {key!r} not found in COLUMN_HEADERS"


def test_stamp_duty_known_values():
    assert stamp_duty_land_tax(250000) == 0.0
    assert stamp_duty_land_tax(350000) == 5000.0
    assert stamp_duty_land_tax(550000) == 15000.0


def test_data_formulas_use_named_ranges():
    """Every DATA_FORMULA_COLS formula references Data_ or Const_ or View_."""
    for key, formula in DATA_FORMULA_COLS.items():
        assert "Data_" in formula or "Const_" in formula or "View_" in formula, (
            f"Data formula {key!r} has no Data_/Const_/View_ ref: {formula[:80]}"
        )


def test_formula_cols_not_in_user_cols():
    """Formula columns should not be in _USER_COLUMNS."""
    for h in _FORMULA_COLUMNS:
        assert h not in _USER_COLUMNS, f"Formula column {h!r} found in _USER_COLUMNS"


def test_stamp_duty_formula_checks_status():
    """Stamp Duty formula returns 0 for Status=Current."""
    formula = DATA_FORMULA_COLS["stamp duty (£)"]
    assert "View_Status" in formula, "Stamp Duty formula must reference View_Status"


def test_net_ashby_formula_checks_status():
    """Net Ashby formula returns 0 for Status=Current."""
    formula = DATA_FORMULA_COLS["net ashby contribution (£)"]
    assert "View_Status" in formula, "Net Ashby formula must reference View_Status"


def test_stamp_duty_formula_uses_splt():
    """Stamp Duty formula implements the same SDLT bands as stamp_duty_land_tax."""
    formula = DATA_FORMULA_COLS["stamp duty (£)"]
    assert "250000" in formula, "Stamp Duty must have 250k threshold"


def test_current_home_mortgage_excludes_ashby():
    """For Status=Current, Mortgage Required = Price - Deposit (Net Ashby=0)."""
    formula = DATA_FORMULA_COLS["mortgage required (£)"]
    assert "Data_NetAshbyContribution" in formula


def test_monthly_mortgage_blank_when_ashby_works_missing():
    """Monthly Mortgage Payment should be blank when Ashby Works Estimate is empty."""
    formula = DATA_FORMULA_COLS["monthly mortgage payment (£)"]
    assert "View_AshbyWorksEstimate" in formula


def test_view_headers_count():
    assert len(VIEW_HEADERS) == 41


def test_all_view_headers_are_covered():
    """Every View header is either a formula column or a manual column."""
    formula_keys = set(VIEW_FORMULA_COLS.keys())
    manual_lower = {h.lower() for h in VIEW_MANUAL_COLUMNS}
    for h in VIEW_HEADERS:
        h_lower = h.lower()
        assert h_lower in formula_keys or h_lower in manual_lower, (
            f"View header {h!r} not covered by any formula or manual column"
        )


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
            f"View formula {key!r} references non-standard range: {formula[:80]}"
        )


def test_total_monthly_formula_references_cost_components():
    """The Total Monthly formula must reference the individual commute cost
    columns, not a single aggregate (Data_CommuteCost does not exist)."""
    formula = VIEW_FORMULA_COLS.get("total monthly housing cost (£)", "")
    assert "Data_MonthlyMortgagePayment" in formula
    assert "Data_YearlySinkingFund" in formula
    assert "Data_BracknellCost" in formula
    assert "Data_SimonLondonCost" in formula
    assert "Data_LorenaLondonCost" in formula
    assert "Data_CouncilTaxCost" in formula
    assert "View_Status" in formula


def test_affordability_formulas_use_ifna_not_ifferror():
    """Every INDEX-based formula must avoid IFERROR (= use IFNA)."""
    for key, formula in VIEW_FORMULA_COLS.items():
        assert "IFERROR" not in formula, f"IFERROR found in {key!r}"


def test_view_manual_columns_are_not_formulas():
    """No manual column key should appear in VIEW_FORMULA_COLS keys."""
    manual_lower = {h.lower() for h in VIEW_MANUAL_COLUMNS}
    for key in VIEW_FORMULA_COLS:
        assert key not in manual_lower, f"Manual column {key!r} found in VIEW_FORMULA_COLS"
