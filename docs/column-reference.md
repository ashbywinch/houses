# Column Reference

> The canonical `COLUMN_HEADERS`, `VIEW_HEADERS`, and `VIEW_FORMULA_COLS` lists in `houses/sheets.py` are the single source of truth for column definitions.
> To add or remove a column, edit `sheets.py` first, then run `make lint` and `make test` to verify alignment.
> To deploy structural changes to the real sheet, run `uv run python scripts/sheet_tool.py migrate-view` (with `--dry-run` first).

## Properties Data Tab (Bot / Server-Written)

45 columns (A–AT). The server writes enriched rows via `sheets.py:_row_values()`. Columns AP–AT are formula-driven (never written by the server).

Key conventions:
- **Primary key**: Rightmove URL (col A). Stable lookup key: Rightmove ID (col H).
- **Monetary values**: floats, no £ prefix. Display formatting is the sheet's job.
- **Missing data**: empty string (never `None`, `0`, or `"N/A"`).
- **User-owned columns** (A–G): server never overwrites these.
- **Formula columns** (AP–AT): populated by Google Sheets formulas, never written by the server.

### Column Listing

| Col | Index | Header | Source |
|-----|-------|--------|--------|
| A | 0 | Rightmove URL | User |
| B | 1 | Address | User |
| C | 2 | Postcode | User |
| D | 3 | Bedrooms | User |
| E | 4 | Price (£) | User |
| F | 5 | Actual Latitude | User |
| G | 6 | Actual Longitude | User |
| H | 7 | Rightmove ID | Server |
| I | 8 | Simon London (min) | Enriched |
| J | 9 | Simon London Cost (£) | Enriched |
| K | 10 | Simon London Route | Enriched |
| L | 11 | Lorena London (min) | Enriched |
| M | 12 | Lorena London Cost (£) | Enriched |
| N | 13 | Lorena London Route | Enriched |
| O | 14 | Bracknell Time (min) | Enriched |
| P | 15 | Bracknell Cost (£) | Enriched |
| Q | 16 | Primary School | Enriched |
| R | 17 | Primary Distance (km) | Enriched |
| S | 18 | Primary Walk (min) | Enriched |
| T | 19 | Primary School Link | Enriched |
| U | 20 | Primary Ofsted | Enriched |
| V | 21 | Primary Inspection Year | Enriched |
| W | 22 | Secondary School | Enriched |
| X | 23 | Secondary Distance (km) | Enriched |
| Y | 24 | Secondary Walk (min) | Enriched |
| Z | 25 | Secondary School Link | Enriched |
| AA | 26 | Secondary Ofsted | Enriched |
| AB | 27 | Secondary Inspection Year | Enriched |
| AC | 28 | Area Description | Enriched |
| AD | 29 | Walk to Town (min) | Enriched |
| AE | 30 | Walkable Amenities | Enriched |
| AF | 31 | EPC Rating | Enriched |
| AG | 32 | Council Tax Band | Enriched |
| AH | 33 | Council Tax Cost (£) | Enriched |
| AI | 34 | Secondary Bus (min) | Enriched |
| AJ | 35 | Secondary Bus Route | Enriched |
| AK | 36 | Approx Latitude (est) | Enriched |
| AL | 37 | Approx Longitude (est) | Enriched |
| AM | 38 | Approx Station CRS | Enriched |
| AN | 39 | Approx Station Name | Enriched |
| AP | 40 | Stamp Duty (£) | Formula |
| AQ | 41 | Net Ashby Contribution (£) | Formula |
| AR | 42 | Mortgage Required (£) | Formula |
| AS | 43 | Monthly Mortgage Payment (£) | Formula |
| AT | 44 | Yearly Sinking Fund (£) | Formula |

### Data Tab Formula Details

**Stamp Duty (AP):** Standard non-first-time-buyer SDLT for England:
- 0% on first £250,000
- 5% on £250,001–£925,000
- 10% on £925,001–£1,500,000
- 12% on portion above £1,500,000

Implemented as: `=IFNA(LET(p,INDEX(Data_Price,ROW()),IF(p<=250000,0,...)))`

**Net Ashby Contribution (AQ):** `Const_GrossAshbyContribution - StampDuty/3 - View_AshbyWorksEstimate`

This formula reads the Ashby Works Estimate from the View tab (manual column AD).

**Mortgage Required (AR):** `Price - Const_Deposit - NetAshby`

**Monthly Mortgage Payment (AS):** `PMT(Const_MortgageRate/12, Const_MortgageTermYears*12, -MortgageRequired)`

**Yearly Sinking Fund (AT):** `Price * Const_SinkingFundRate`

## Properties View Tab (Human / Formula-Driven)

38 columns (A–AL). The View tab uses INDEX-based formulas with named ranges (`Data_*`, `View_*`, `Const_*`) instead of hardcoded column letters, so they survive column insertions/reorders in the Data tab.

The tab has 5 logical zones separated by thin gap columns (16px, no header):

### Zone 1: Listing (A–E)

| Col | Header | Source |
|-----|--------|--------|
| A | Listing Address | Manual |
| B | Rightmove Link | Manual |
| C | Rightmove ID | Formula (from link) |
| D | Purchase Cost (£) | Index from Data |
| E | EPC Rating | Index from Data |

### Zone 2: Commute & Area (G–N)

| Col | Header | Source |
|-----|--------|--------|
| G | Simon London | Formula (min/1440) |
| H | Simon London Route | Index from Data |
| I | Lorena London | Formula (min/1440) |
| J | Lorena London Route | Index from Data |
| K | Bracknell Time | Formula (min/1440) |
| L | What the Area is Like | Index from Data |
| M | Walk to Town | Formula (min/1440) |
| N | Walkable Amenities | Index from Data |

### Zone 3: Schools (P–Y)

| Col | Header | Source |
|-----|--------|--------|
| P | Primary School | Hyperlink from Data |
| Q | Primary Walk | Formula (min/1440) |
| R | Primary Ofsted | Index from Data |
| S | Primary Inspection Year | Index from Data |
| T | Secondary School | Hyperlink from Data |
| U | Secondary Walk | Formula (min/1440) |
| V | Secondary Ofsted | Index from Data |
| W | Secondary Inspection Year | Index from Data |
| X | Secondary Bus | Formula (min/1440) |
| Y | Secondary Bus Route | Index from Data |

### Zone 4: Affordability — Monthly Costs (AA–AF)

| Col | Header | Formula |
|-----|--------|---------|
| AA | Monthly Mortgage Payment (£) | Index from Data |
| AB | Monthly Sinking Fund (£) | Data_SinkingFund/12 |
| AC | Monthly Life Insurance (£) | Const_LifeInsuranceMonthly constant |
| AD | Monthly Commute Cost (£) | 46×(Bracknell+Simon+2×Lorena)/12 |
| AE | Monthly Council Tax (£) | Data_CouncilTaxCost/12 |
| AF | Total Monthly Housing Cost (£) | Sum of AA–AE |

### Zone 5: User Inputs & Notes (AH–AL)

| Col | Header | Source |
|-----|--------|--------|
| AH | Ashby Works Estimate (£) | Manual — user enters works estimate |
| AI | Group Notes / WhatsApp | Manual |
| AJ | Ashby comments | Manual |
| AK | Status | Manual (No/Maybe dropdown) |
| AL | Status Reason | Manual |

### Gap Columns

Columns F (5), O (14), Z (25), AG (32) are thin gap columns (16px) that prevent the adjacent column groups from merging into one. They have no header and no formula.

### Column Groups

The View tab has 5 independent collapsible column groups corresponding to the zones above. Click the +/- buttons in the column headers to collapse/expand each zone independently. The gap columns between zones prevent the groups from merging.

## Constants Tab (new)

| Cell | Constant | Value | Named Range |
|------|----------|-------|-------------|
| B2 | Current Sale Price (£) | 550000 | `Const_CurrentSalePrice` |
| B3 | Outstanding Mortgage (£) | 373000 | `Const_OutstandingMortgage` |
| B4 | Deposit Amount (£) | `=B2-B3` (=177000) | `Const_Deposit` |
| B5 | Gross Ashby Contribution (£) | 300000 | `Const_GrossAshbyContribution` |
| B6 | Mortgage Interest Rate | 0.0495 | `Const_MortgageRate` |
| B7 | Mortgage Term (years) | 27 | `Const_MortgageTermYears` |
| B8 | Life Insurance Monthly (£) | 150 | `Const_LifeInsuranceMonthly` |
| B9 | Sinking Fund Rate (annual) | 0.01 | `Const_SinkingFundRate` |

Row 1 is a header row (A1="Constant", B1="Value"). Named ranges point to the Value cells (column B, rows 2–9).

The Deposit formula is written as a formula string (`=B2-B3`) with `USER_ENTERED` so Google Sheets evaluates it.

## Removed Columns

The following columns were removed from the View tab in the affordability restructure:
- **Yearly Commute Total (£)** — replaced by Monthly Commute Cost (£) (col AA)
- **Yearly Council Tax (£)** — replaced by Monthly Council Tax (£) (col AB)

These values are now computed as monthly values in the affordability block.

## Named Ranges

All named ranges follow a three-prefix convention:
- `Data_*` — columns on the Properties Data tab (auto-generated from COLUMN_HEADERS)
- `View_*` — columns on the Properties View tab (used for cross-tab references)
- `Const_*` — single cells on the Constants tab

To refresh named ranges after a column operation:
```bash
uv run python scripts/sheet_tool.py refresh-formulas
```

## Conditional Formatting

The View tab uses conditional formatting to color-code:
- **EPC Rating**: A/B green, C/D orange, E/F/G red
- **Commute times**: Simon/Lorena/Bracknell/Walk to Town/Walk times
- **Ofsted ratings**: Outstanding green, Good orange, RI/Inadequate red
- **Inspection years**: >=2023 green, <=2022 orange
- **Grey text row**: entire row grey when Status = "No"

Thresholds and rules are defined in `houses/sheets.py:sync_view_formulas()`.

## Update Process

To add or modify a column:

1. Edit `COLUMN_HEADERS` or `VIEW_HEADERS` in `houses/sheets.py`
2. If adding an enriched Data column, update `_row_values()` in `houses/sheets.py`
3. If adding a View formula, add an entry to `VIEW_FORMULA_COLS` in `houses/sheets.py`
4. If adding a manual View column, add to `VIEW_MANUAL_COLUMNS` in `houses/sheets.py`
5. If adding a Data formula column, add to `DATA_FORMULA_COLS` in `houses/sheets.py`
6. Update `_FORMULA_COLUMNS` if the server should not write to the new column
7. Update `tests/unit/test_sheets.py` — column counts, test data, invariants
8. Update `tests/unit/test_server.py` — `DATA_HEADERS` and `VIEW_HEADERS`
9. Run `make test` to verify alignment
10. For structural changes to the sheet, use `sheet_tool.py migrate-view` (with `--dry-run` first)
11. For non-structural changes, run `uv run python scripts/sheet_tool.py refresh-formulas`
