# Implementation Plan: Affordability Enhancement

## Sinking Fund: 1% of purchase price annually (configurable)

---

## View Tab — Target Layout (34 columns, A–AH)

First 4 columns (A–D) unchanged. Yearly Commute and Yearly Council Tax removed — moved into affordability block as monthly values. All other data columns shift left by 2. Affordability block inserted between schools and user notes.

**Zone 1: Listing** (A–E, unchanged from current A–E)
```
A (0)  Listing Address              manual
B (1)  Rightmove Link               manual
C (2)  Rightmove ID                 formula
D (3)  Purchase Cost (£)            formula
E (4)  EPC Rating                   formula
```

**Zone 2: Commute & Area** (F–M, shifted left from current H–O)
```
F (5)  Simon London                 formula
G (6)  Simon London Route           formula
H (7)  Lorena London                formula
I (8)  Lorena London Route          formula
J (9)  Bracknell Time               formula
K (10) What the Area is Like        formula
L (11) Walk to Town                 formula
M (12) Walkable Amenities           formula
```

**Zone 3: Schools** (N–W, shifted left from current P–Y)
```
N (13) Primary School               formula
O (14) Primary Walk                 formula
P (15) Primary Ofsted               formula
Q (16) Primary Inspection Year      formula
R (17) Secondary School             formula
S (18) Secondary Walk               formula
T (19) Secondary Ofsted             formula
U (20) Secondary Inspection Year    formula
V (21) Secondary Bus                formula
W (22) Secondary Bus Route          formula
```

**Zone 4: Affordability — Monthly Costs Only** (X–AC, 6 cols)
```
X (23) Monthly Mortgage Payment (£)   formula  — PMT amortization
Y (24) Monthly Sinking Fund (£)       formula  — 1% of Price ÷ 12
Z (25) Monthly Life Insurance (£)     formula  — £150 constant
AA (26) Monthly Commute Cost (£)      formula  — yearly ÷ 12
AB (27) Monthly Council Tax (£)       formula  — yearly ÷ 12
AC (28) Total Monthly Housing Cost (£) formula  — sum of X–AB
```

**Zone 5: User Inputs & Notes** (AD–AH, 5 cols)
```
AD (29) Ashby Works Estimate (£)      MANUAL   — user enters works estimate
AE (30) Group Notes / WhatsApp        MANUAL   — shifted from Z
AF (31) Ashby comments                MANUAL   — shifted from AA
AG (32) Status                        MANUAL   — shifted from AB
AH (33) Status Reason                 MANUAL   — shifted from AC
```

---

## Constants Tab (new)

| Cell | Constant | Value | Named Range |
|------|----------|-------|-------------|
| B1 | Current Sale Price (£) | 550000 | `Const_CurrentSalePrice` |
| B2 | Outstanding Mortgage (£) | 373000 | `Const_OutstandingMortgage` |
| B3 | Deposit Amount (£) | `=B1-B2` (=177000) | `Const_Deposit` |
| B4 | Gross Ashby Contribution (£) | 300000 | `Const_GrossAshbyContribution` |
| B5 | Mortgage Interest Rate | 0.0495 | `Const_MortgageRate` |
| B6 | Mortgage Term (years) | 27 | `Const_MortgageTermYears` |
| B7 | Life Insurance Monthly (£) | 150 | `Const_LifeInsuranceMonthly` |
| B8 | Sinking Fund Rate (annual) | 0.01 | `Const_SinkingFundRate` |

---

## Data Tab — New Columns (5 cols, AP–AT, indices 40–44)

| Col | Header | Formula |
|-----|--------|---------|
| AP | Stamp Duty (£) | SDLT from Price (0%/5%/10%/12% bands) |
| AQ | Net Ashby Contribution (£) | Gross Ashby - SDLT/3 - View_AshbyWorksEstimate |
| AR | Mortgage Required (£) | Price - Const_Deposit - Net Ashby |
| AS | Monthly Mortgage Payment (£) | PMT(MortgageRate/12, 27*12, -MortgageRequired) |
| AT | Yearly Sinking Fund (£) | Price × Const_SinkingFundRate |

**No Ashby Works Estimate column on Data tab** — the Net Ashby formula (AQ) reads the View tab value via `INDEX(View_AshbyWorksEstimate, ROW())`.

---

## Implementation: 7 Test-First Slices

Each slice follows: write failing test → implement → verify green → user-visible outcome.

---

### Slice 1: Constants Tab + Const_ Named Ranges

**Test(s) to write** (`tests/unit/test_sheets.py`):
- `test_const_range_name_generates_correct_prefix` — `_const_range_name("Mortgage Interest Rate")` → `"Const_MortgageInterestRate"`
- `test_const_range_name_is_deterministic` — all constant names are stable
- `test_ensure_constants_tab_creates_headers` — mock spreadsheet, verify `ensure_constants_tab()` writes `CONSTANTS_HEADERS` as row 1 and values as row 2
- `test_ensure_named_ranges_includes_const` — after `ensure_named_ranges()`, `Const_MortgageRate` exists pointing to `'Constants'!B5`

**Code to write** (`houses/sheets.py`):
```python
CONSTANTS_TAB = "Constants"
CONSTANTS_HEADERS: list[str] = [
    "Current Sale Price (£)", "Outstanding Mortgage (£)", "Deposit Amount (£)",
    "Gross Ashby Contribution (£)", "Mortgage Interest Rate",
    "Mortgage Term (years)", "Life Insurance Monthly (£)", "Sinking Fund Rate (annual)",
]
CONSTANTS_VALUES: list[tuple[str, str]] = [
    ("Current Sale Price (£)", "550000"),
    ("Outstanding Mortgage (£)", "373000"),
    ("Deposit Amount (£)", "=INDIRECT(ADDRESS(ROW()-2,COLUMN()))-INDIRECT(ADDRESS(ROW()-1,COLUMN()))"),
    ("Gross Ashby Contribution (£)", "300000"),
    ("Mortgage Interest Rate", "0.0495"),
    ("Mortgage Term (years)", "27"),
    ("Life Insurance Monthly (£)", "150"),
    ("Sinking Fund Rate (annual)", "0.01"),
]
```

Wait, the deposit formula is tricky. It should be `=B1-B2` in Google Sheets. If I write it as a formula string, it needs to be a valid formula. Let me use:
```python
("Deposit Amount (£)", "=B1-B2"),
```

Add `_const_range_name(header: str) -> str` — same as `named_range_name` but with `Const_` prefix.

Add `ensure_constants_tab(sh: gspread.Spreadsheet) -> None`:
1. Create tab if not exists
2. Write headers (row 1: col A = "Constant", col B = "Value")
3. Write constant values (rows 2-9: col A = label, col B = value/formula)
4. Set number formats for currency cells

Update `ensure_named_ranges()` to:
1. Call `ensure_constants_tab()`
2. Create `Const_*` named ranges for each constant (single-cell ranges)
3. Add `or name.startswith("Const_")` to orphan cleanup

**User outcome**: Run `uv run python scripts/setup_sheet.py` → Constants tab appears in the sheet with correct values.

---

### Slice 2: Data Tab — Add Formula Columns

**Test(s) to write** (`tests/unit/test_sheets.py`):
- `test_data_headers_count` — `len(COLUMN_HEADERS)` is 45 (was 40)
- `test_data_formula_count` — `len(DATA_FORMULA_COLS)` is 5, each key maps to a header cell in `COLUMN_HEADERS`
- `test_stamp_duty_known_values` — call a pure function `_splt(price)` → verify £350k→£5k, £550k→£15k, £925k→£33,750
- `test_data_formulas_use_named_ranges` — every formula string in `DATA_FORMULA_COLS` contains `Data_` or `Const_` or `View_` named ranges
- `test_formula_cols_not_in_row_values` — formula column headers are NOT expected in `_row_values` output (update `test_row_values_contains_all_enriched_columns` to exclude them)
- `test_formula_cols_not_in_user_cols` — formula columns are NOT in `_USER_COLUMNS`

**Code to write** (`houses/sheets.py`):
- Extract `_splt(price: float) -> float` pure function for SDLT
- Append 5 headers to `COLUMN_HEADERS`
- Add `DATA_FORMULA_COLS: dict[str, str]` — lowercase header → formula string
- Add `sync_data_formulas(spreadsheet) -> None` — formula writing (rows 2–1000)
- Update `ensure_named_ranges` — `Data_*` ranges auto-created for new headers

**Update existing tests**:
- `test_row_values_contains_all_enriched_columns` — exclude formula columns
- `test_row_values_with_full_enrichment` — expected set excludes formula columns
- `test_named_range_name_is_deterministic` — includes new headers

**User outcome**: Run `uv run python scripts/sheet_tool.py refresh-formulas` → Data tab columns AP–AT appear with formulas.

---

### Slice 3: View Tab — Update VIEW_HEADERS + VIEW_FORMULA_COLS

**Test(s) to write** (`tests/unit/test_sheets.py`):
- `test_view_headers_count` — `len(VIEW_HEADERS)` is 34 (was 29)
- `test_all_view_headers_are_covered` — every header is in `VIEW_FORMULA_COLS` or `VIEW_MANUAL_COLUMNS`
- `test_ashby_works_in_manual_columns` — `"Ashby Works Estimate (£)"` in `VIEW_MANUAL_COLUMNS`
- `test_removed_headers_gone` — `"Yearly Commute Total (£)"` and `"Yearly Council Tax (£)"` no longer in `VIEW_HEADERS`
- `test_view_formulas_use_named_ranges` — extended with 6 new affordability formulas
- `test_total_monthly_formula_includes_all_components` — verify the Total formula references Mortgage, Sinking, Life Insurance, Commute, Council Tax

**Additional validation**: run existing `test_view_formulas_use_named_ranges` and `test_xlookup_key_is_typed_as_number` — they should pass with new formulas added.

**Code to write** (`houses/sheets.py`):
- Rewrite `VIEW_HEADERS` as 34-column list
- Remove `"Yearly Council Tax (£)"` from `VIEW_MANUAL_COLUMNS`
- Add `"Ashby Works Estimate (£)"` to `VIEW_MANUAL_COLUMNS`
- Remove `"yearly commute total (£)"` from `VIEW_FORMULA_COLS`
- Add 6 entries to `VIEW_FORMULA_COLS` for affordability columns
- Remove `"yearly council tax (£)"` from `.keys()` (was manual, now column removed)

**Formula details for VIEW_FORMULA_COLS**:

| Key | Formula |
|-----|---------|
| `monthly mortgage payment (£)` | `=IFNA(INDEX(Data_MonthlyMortgagePayment,ROW()),)` |
| `monthly sinking fund (£)` | `=IFNA(INDEX(Data_YearlySinkingFund,ROW())/12,)` |
| `monthly life insurance (£)` | `=IFNA(Const_LifeInsuranceMonthly,)` |
| `monthly commute cost (£)` | `=IFNA(LET(k,IFNA(INDEX(Data_BracknellCost,ROW()),),g,IFNA(INDEX(Data_SimonLondonCost,ROW()),),i,IFNA(INDEX(Data_LorenaLondonCost,ROW()),),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)/12)),)` |
| `monthly council tax (£)` | `=IFNA(INDEX(Data_CouncilTaxCost,ROW())/12,)` |
| `total monthly housing cost (£)` | `=IFNA(LET(p,IFNA(INDEX(Data_MonthlyMortgagePayment,ROW()),)+IFNA(INDEX(Data_YearlySinkingFund,ROW())/12,)+Const_LifeInsuranceMonthly+IFNA(INDEX(AA:AA,ROW()),)+IFNA(INDEX(AB:AB,ROW()),),IF(p=0,"",p)),)` |

**User outcome**: `make test` passes. View tab definitions are correct. Actual sheet still has old layout — migration script fixes that.

---

### Slice 4: Migration Script — `migrate-view` Command

**Test(s) to write**:
- `tests/unit/test_sheets.py`: `test_migrate_view_dry_run_output` — call `cmd_migrate_view(dry_run=True)` with test sheet, verify it prints expected operations
- Integration-level: test with a test sheet (creates dummy View tab, runs migration, verifies structure)

**Code to write** (`scripts/sheet_tool.py`):

```python
def cmd_migrate_view(dry_run: bool = False):
    """
    1. Delete columns F (Yearly Commute Total) and G (Yearly Council Tax)
    2. Insert 7 columns at position 23 (after Secondary Bus Route, before Group Notes)
    3. Write new headers for cols X–AH
    4. Write affordability formulas via sync_view_formulas()
    5. Write data formulas via sync_data_formulas()
    6. Update column count and ensure named ranges
    """
```

Migration steps:
1. Open sheet, get View tab, get sheetId
2. Read all current data (to back up user notes if needed)
3. Build Sheets API batch with:
   - `deleteDimension` for cols 5–6 (F–G: Yearly Commute, Council Tax)
   - `insertDimension` for 7 cols at position 23 (post-delete index)
4. Write all 34 new headers to row 1
5. Call `ensure_named_ranges(sh)` — creates/updates all Data_, View_, Const_ ranges
6. Call `sync_data_formulas(sh)` — writes Data tab formulas
7. Call `sync_view_formulas(sh)` — writes View tab formulas + formatting
8. In dry-run mode: just print operations without executing

**Edge cases**:
- Empty rows (no data beyond header)
- Existing user notes data (preserved by insertDimension shifting columns)
- Missing expected columns (abort with clear message)
- Already-migrated sheet (detect by checking if new headers exist)

**User outcome**: `uv run python scripts/sheet_tool.py migrate-view --dry-run` → prints expected changes; running without `--dry-run` → View tab restructured with all formulas working.

---

### Slice 5: Formatting + Polish

**Test(s)**: None new — verify visually. The existing `test_view_formulas.py` tests verify that values populate correctly.

**Code to write** (`houses/sheets.py: sync_view_formulas`):
- Add currency format (`£#,##0.00`) for cols X–AC, AD
- Add grey text formatting for Z (Monthly Life Insurance — constant, visually distinct)
- Add bold formatting for AC (Total Monthly Housing Cost)
- Add wrap formatting for AD (Ashby Works Estimate — manual input notes)

**Also update** `setup_sheet.py`:
1. Import and create Constants tab
2. Update View formula row count

**User outcome**: Run `refresh-formulas` → affordability columns are formatted with currency, grey constant, bold total.

---

### Slice 6: Integration/E2E Tests

**Test(s) to update** (`tests/integration/test_view_formulas.py`):
- Update `_TestRecord` — no Yearly Commute/Council Tax fields needed; add `ashby_works_estimate` if needed
- Update `RECORDS` — remove commute total and council tax yearly values from test data (they're formula/manual columns being removed)
- Update `to_data_row` — add new Data columns (Ashby Works not on Data, so no change needed)
- Update `test_purchase_cost_populated` — verify affordability data still works
- Add `test_monthly_commute_cost_calculated` — verify 46*(costs)/12
- Add `test_total_monthly_housing_cost` — verify sum includes all 5 components
- Add `test_ashby_works_column_is_manual` — verify no formula written to AD
- Remove `test_yearly_commute_calculated_correctly` (column is gone)
- Add `test_all_formula_columns_have_no_na` — already exists, just verify it passes
- Update `test_new_row_gets_formulas_after_sync` — update column count, formula length
- Update `test_all_view_headers_are_covered` — already exists, verify it passes
- Update formula list in `test_view_formulas_use_named_ranges`
- Update formula list in `test_xlookup_key_is_typed_as_number`
- Add `test_column_headers_synced_with_test_data` — validate test data against canonical headers

**User outcome**: `make test` passes at both unit and integration level.

---

### Slice 7: Documentation

**Files**: `docs/column-reference.md`
- Document new Data columns (AP–AT)
- Document new View columns (34-column layout with affordability formulas)
- Document Constants tab structure
- Document removed columns (Yearly Commute Total, Yearly Council Tax)
- Update update process checklist

---

## Migration Script Details

The `migrate-view` command in `scripts/sheet_tool.py` is the critical transition piece. Here's how it works step-by-step:

### Pre-flight validation
```python
def cmd_migrate_view(dry_run: bool = False):
    sh, ws = _get_sheet()
    view_ws = sh.worksheet(VIEW_TAB)
    sid = view_ws._properties["sheetId"]
    headers = view_ws.get_all_values()[0]
    
    # Validate expected current layout
    expected = ["Listing Address", "Rightmove Link", "Rightmove ID", "Purchase Cost (£)",
                 "EPC Rating", "Yearly Commute Total (£)", "Yearly Council Tax (£)",
                 "Simon London", ...]
    for i, exp in enumerate(expected):
        if i >= len(headers) or headers[i].strip() != exp:
            print(f"ERROR: Expected col {i} ({col_letter(i)}) to be '{exp}', got '{headers[i] if i < len(headers) else 'MISSING'}'")
            sys.exit(1)
    
    if dry_run:
        print("[DRY RUN] Would perform:")
        print("  1. Delete columns F–G (Yearly Commute Total, Yearly Council Tax)")
        print("  2. Insert 7 columns at position 23 (after Secondary Bus Route)")
        print("  3. Write 34 new headers to row 1")
        print("  4. Refresh named ranges, Data formulas, View formulas")
        return
    # ... actual execution
```

### Actual migration
```python
    requests = []
    
    # Step 1: Delete cols F–G (indices 5–6)
    requests.append({
        "deleteDimension": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 5, "endIndex": 7}
        }
    })
    
    # Step 2: Insert 7 cols at position 23 (post-delete index)
    requests.append({
        "insertDimension": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 23, "endIndex": 30},
            "inheritFromBefore": False
        }
    })
    
    sh.batch_update({"requests": requests})
    
    # Step 3: Write 34 new headers
    from houses.sheets import VIEW_HEADERS
    for i, h in enumerate(VIEW_HEADERS):
        view_ws.update_acell(f"{col_letter(i)}1", h)
    
    # Steps 4–6: Refresh everything
    from houses.sheets import ensure_named_ranges, sync_view_formulas, sync_data_formulas
    ensure_named_ranges(sh)
    sync_data_formulas(sh)
    sync_view_formulas(sh)
```

### Recovery
If migration fails partway, the `--undo` flag can restore from backup:
```
uv run python scripts/sheet_tool.py migrate-view --undo
```
This reverses the operations (delete inserted cols, re-add deleted cols, restore headers from backup).

---

## Constants Tab: Deposit Formula

The Deposit is `=B1-B2` in Google Sheets. The constant tab stores this as a formula string. When `ensure_constants_tab` writes it, it uses `value_input_option="USER_ENTERED"` so Google Sheets evaluates it as a formula.

---

## SDLT Calculation (`_splt`)

```python
def _splt(price: float) -> float:
    """Standard non-first-time-buyer SDLT for England."""
    if price <= 250000:
        return 0.0
    if price <= 925000:
        return (price - 250000) * 0.05
    if price <= 1500000:
        return (price - 925000) * 0.10 + 33750.0
    return (price - 1500000) * 0.12 + 91250.0
```

This pure function is used both in the Data tab formula generation AND in unit tests.

---

## Files Summary

| File | Slice | Changes |
|------|-------|---------|
| `houses/sheets.py` | 1–3, 5 | Constants tab, Data formulas, View layout, formatting, named ranges |
| `scripts/setup_sheet.py` | 1, 3 | Constants tab creation, View layout |
| `scripts/sheet_tool.py` | 4 | `migrate-view` command |
| `docs/column-reference.md` | 7 | Document all changes |
| `tests/unit/test_sheets.py` | 1–4 | Column counts, SDLT, formula tests, migration dry-run |
| `tests/integration/test_view_formulas.py` | 6 | Affordability E2E tests |

---

## Execution Order

Run in sequence — each slice is independently verifiable and doesn't depend on the next:

```
1. Constants tab + Const_ named ranges
   → make test, then setup_sheet.py
   
2. Data tab formula columns
   → make test, then refresh-formulas
   
3. View tab header/formula definitions
   → make test (no sheet changes needed)
   
4. Migration script
   → sheet_tool.py migrate-view --dry-run, then for real
   
5. Formatting
   → refresh-formulas
   
6. Integration tests
   → make test (requires real/test sheet)
   
7. Documentation
```
