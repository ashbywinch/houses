"""One-time setup: create Properties Data, Properties View, and Constants tabs.

View tab formulas use Google Sheets named ranges, so they survive column
insertions and reorders in the Data tab.

The canonical column header list lives in houses/sheets.py — this script imports it
rather than duplicating it. See docs/column-reference.md for the full layout.

Usage: uv run python scripts/setup_sheet.py
"""

import json
import os
import sys

import gspread
from google.oauth2.service_account import Credentials

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from houses.sheets import (  # noqa: E402
    COLUMN_HEADERS,
    CONSTANTS_TAB,
    VIEW_FORMULA_COLS,
    VIEW_HEADERS,
    VIEW_MANUAL_COLUMNS,
    col_letter,
    ensure_constants_tab,
    ensure_named_ranges,
    sync_data_formulas,
    sync_view_formulas,
)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

DATA_TAB = "Properties Data"
VIEW_TAB = "Properties View"


def main():
    raw = os.environ["GOOGLE_SHEETS_SERVICE_ACCOUNT"]
    sheet_id = os.environ["HOUSES_SHEET_ID"]

    creds = Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    existing = {w.title for w in sh.worksheets()}

    # Data tab
    if DATA_TAB not in existing:
        ws_data = sh.add_worksheet(title=DATA_TAB, rows=500, cols=len(COLUMN_HEADERS))
        ws_data.append_row(COLUMN_HEADERS, value_input_option="USER_ENTERED")
        print(f"Created '{DATA_TAB}' tab with {len(COLUMN_HEADERS)} columns")
    else:
        print(f"'{DATA_TAB}' tab already exists — leaving untouched")

    # View tab
    if VIEW_TAB not in existing:
        ws_view = sh.add_worksheet(title=VIEW_TAB, rows=500, cols=len(VIEW_HEADERS))
        ws_view.append_row(VIEW_HEADERS, value_input_option="USER_ENTERED")
        print(f"Created '{VIEW_TAB}' tab with {len(VIEW_HEADERS)} columns")
    else:
        ws_view = sh.worksheet(VIEW_TAB)
        print(f"'{VIEW_TAB}' tab already exists — leaving headers untouched")

    # Constants tab
    ensure_constants_tab(sh)
    if CONSTANTS_TAB not in existing:
        print(f"Created '{CONSTANTS_TAB}' tab")
    else:
        print(f"'{CONSTANTS_TAB}' tab already exists — leaving untouched")

    # Build named ranges
    ensure_named_ranges(sh)

    # Build View formula row dynamically from VIEW_FORMULA_COLS + manual columns
    manual_lower = {h.lower() for h in VIEW_MANUAL_COLUMNS}
    formulas = []
    for h in VIEW_HEADERS:
        key = h.lower()
        if key in VIEW_FORMULA_COLS:
            formulas.append(VIEW_FORMULA_COLS[key])
        elif key in manual_lower:
            formulas.append("")
        else:
            raise AssertionError(
                f"View header {h!r} has neither a formula entry nor is listed "
                "in VIEW_MANUAL_COLUMNS. Add it to one or the other."
            )

    last_col = col_letter(len(formulas) - 1)
    ws_view.update(
        range_name=f"A2:{last_col}2",
        values=[formulas],
        value_input_option="USER_ENTERED",
    )

    # Write Data tab formulas
    sync_data_formulas(sh)

    print(f"Column count: Data={len(COLUMN_HEADERS)}, View={len(formulas)}")
    print("Done!")


if __name__ == "__main__":
    main()
