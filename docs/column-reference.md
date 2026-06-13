# Column Reference

> The canonical `COLUMN_HEADERS` list lives on the `Row` class (`houses/sheets/row.py`). `VIEW_HEADERS` and `VIEW_FORMULA_COLS` are in `houses/sheets/formulas.py`. These are the single source of truth for column definitions.
> To add or remove a column, edit `Row.HEADERS` in `houses/sheets/row.py` first, then run `make lint` and `make test` to verify alignment.
> To deploy structural changes to the real sheet, run `uv run python scripts/sheet_tool.py migrate-view` (with `--dry-run` first).

## Properties Data Tab (Bot / Server-Written)

45 columns (A–AT). The server writes enriched rows via `Row.from_property()` in `houses/sheets/row.py`. Columns AP–AT are formula-driven (never written by the server).

Key conventions:
- **Primary key**: Rightmove URL (col A). Stable lookup key: Rightmove ID (col H).
- **Monetary values**: floats, no £ prefix. Display formatting is the sheet's job.
- **Missing data**: empty string (never `None`, `0`, or `"N/A"`).
- **User-owned columns** (A–G): server never overwrites these.
- **Formula columns** (AP–AT): populated by Google Sheets formulas, never written by the server.

## Properties View Tab (Human / Formula-Driven)

38 columns (A–AL). The View tab uses INDEX-based formulas with named ranges (`Data_*`, `View_*`, `Const_*`) instead of hardcoded column letters, so they survive column insertions/reorders in the Data tab.

The tab has 5 logical zones separated by thin gap columns (16px, no header) and grouped using gsheets collapsible column groups.

1: Key info
2: Commute & Area 
3: Schools 
4: Affordability
5: User Inputs & Notes 

### Gap Columns

Empty columns between groups prevent the adjacent column groups from merging into a single group. They have no header and no formula.

## Constants Tab 

Named ranges for user constants live here for use in formulas.
Row 1 is a header row (A1="Constant", B1="Value"). Named ranges point to the Value cells (column B, rows 2–9).

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

The View tab uses conditional formatting to color-code cells as a shorthand for how the information in the cell affects the desirability of buying the house.

- **EPC Rating**: A/B green, C/D orange, E/F/G red
- **Commute times**: Simon/Lorena/Bracknell/Walk to Town/Walk times
- **Ofsted ratings**: Outstanding green, Good orange, RI/Inadequate red
- **Inspection years**: >=2023 green, <=2022 orange
- **Grey text row**: entire row grey when Status = "No"

Thresholds and rules are defined in `houses/sheets/view.py:View.sync()` and `houses/sheet_presentation.py`.
