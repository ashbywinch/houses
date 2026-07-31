# Column Reference

> **The canonical column definitions live in code:** `Row.HEADERS` / `Row.from_property()` in `houses/sheets/row.py`; `VIEW_HEADERS` and `VIEW_FORMULA_COLS` in `houses/sheets/formulas.py`. This page records only the **conventions and invariants** that aren't discoverable from those files.
>
> To add/remove a column: edit `Row.HEADERS` first, then `make lint` + `make test`. To deploy structural changes: `uv run python scripts/sheet_tool.py migrate-view` (`--dry-run` first).

## Properties Data Tab (server-written)

Key conventions:

- **Primary key**: Rightmove URL (col A). Stable lookup key: Rightmove ID (col H).
- **Monetary values**: floats, no £ prefix — display formatting is the sheet's job.
- **Missing data**: empty string (never `None`, `0`, or `"N/A"`).
- **User-owned columns** (A–G): server never overwrites these.
- **Formula columns** (AP–AT): populated by Google Sheets formulas, never by the server.

## Properties View Tab (formula-driven)

Uses INDEX-based formulas with **named ranges** (`Data_*`, `View_*`, `Const_*`) instead of hardcoded column letters, so they survive column insertions/reorders in the Data tab.

5 logical zones separated by thin gap columns (16px, no header, no formula — prevents adjacent groups merging):

1. Key info
2. Commute & Area
3. Schools
4. Affordability
5. User Inputs & Notes

## Constants Tab

Named ranges for user constants. Row 1 is a header row (A1="Constant", B1="Value"); named ranges point to Value cells (column B, rows 2–9).

## Named ranges

Three-prefix convention:

- `Data_*` — Data tab columns (auto-generated from COLUMN_HEADERS)
- `View_*` — View tab columns (cross-tab references)
- `Const_*` — single cells on the Constants tab

Refresh after a column operation: `uv run python scripts/sheet_tool.py refresh-formulas`.

## Conditional formatting

Color-codes cells as desirability shorthand:

- **EPC Rating**: A/B green, C/D orange, E/F/G red
- **Commute times**: Simon/Lorena/Bracknell/Walk to Town/Walk times
- **Ofsted ratings**: Outstanding green, Good orange, RI/Inadequate red
- **Inspection years**: ≥2023 green, ≤2022 orange
- **Grey text row**: entire row grey when Status = "No"

Thresholds and rules are defined in `houses/sheets/view.py:View.sync()` and `houses/sheet_presentation.py`.
