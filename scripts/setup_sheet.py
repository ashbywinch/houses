"""One-time setup: create Properties Data and Properties View tabs with XLOOKUP formulas.

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
from houses.sheets import COLUMN_HEADERS, VIEW_HEADERS, col_letter, ensure_named_ranges, named_range_name  # noqa: E402

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

    if DATA_TAB not in existing:
        ws_data = sh.add_worksheet(title=DATA_TAB, rows=500, cols=len(COLUMN_HEADERS))
        ws_data.append_row(COLUMN_HEADERS, value_input_option="USER_ENTERED")
        print(f"Created '{DATA_TAB}' tab with {len(COLUMN_HEADERS)} columns")
    else:
        print(f"'{DATA_TAB}' tab already exists — leaving untouched")

    if VIEW_TAB not in existing:
        ws_view = sh.add_worksheet(title=VIEW_TAB, rows=500, cols=len(VIEW_HEADERS))
        ws_view.append_row(VIEW_HEADERS, value_input_option="USER_ENTERED")
        print(f"Created '{VIEW_TAB}' tab with {len(VIEW_HEADERS)} columns")
    else:
        ws_view = sh.worksheet(VIEW_TAB)
        print(f"'{VIEW_TAB}' tab already exists — leaving headers untouched")

    # Build named range references dynamically from COLUMN_HEADERS
    ensure_named_ranges(sh)

    lookup_key = "VALUE(INDEX(View_RightmoveID, ROW()))"
    link_formula = "INDEX(View_RightmoveLink, ROW())"
    named_range = named_range_name
    rid_range = named_range("Rightmove ID")

    formulas = [
        "",  # A: Listing Address (manual)
        "",  # B: Rightmove Link (manual)
        f'=REGEXEXTRACT({link_formula},"properties/(\\d+)")',                                                       # C: ID from URL
        f'=XLOOKUP({lookup_key},{rid_range},{named_range("Price (£)")}    )',                                       # D: Purchase Cost
        f'=XLOOKUP({lookup_key},{rid_range},{named_range("EPC Rating")}    )',                                      # E: EPC Rating
        f'=LET(k,XLOOKUP({lookup_key},{rid_range},{named_range("Bracknell Cost (£)")}),g,XLOOKUP({lookup_key},{rid_range},{named_range("Simon London Cost (£)")}),i,XLOOKUP({lookup_key},{rid_range},{named_range("Lorena London Cost (£)")}),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)))',  # F: Commute Total
        "",                                                                                               # G: Council Tax (manual)
        f'=LET(v,XLOOKUP({lookup_key},{rid_range},{named_range("Simon London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',        # H: Simon London
        f'=LET(v,XLOOKUP({lookup_key},{rid_range},{named_range("Lorena London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',       # I: Lorena London
        f'=LET(v,XLOOKUP({lookup_key},{rid_range},{named_range("Bracknell Time (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',      # J: Bracknell Time
        f'=XLOOKUP({lookup_key},{rid_range},{named_range("Area Description")}     )',                                             # K: Area
        f'=LET(v,XLOOKUP({lookup_key},{rid_range},{named_range("Walk to Town (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',        # L: Walk to Town
        f'=XLOOKUP({lookup_key},{rid_range},{named_range("Walkable Amenities")}   )',                                             # M: Amenities
        f'=HYPERLINK(XLOOKUP({lookup_key},{rid_range},{named_range("Primary School Link")}),XLOOKUP({lookup_key},{rid_range},{named_range("Primary School")}))',  # N: Primary School
        f'=LET(v,XLOOKUP({lookup_key},{rid_range},{named_range("Primary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',        # O: Primary Walk
        f'=XLOOKUP({lookup_key},{rid_range},{named_range("Primary Ofsted")}       )',                                             # P: Primary Ofsted
        f'=XLOOKUP({lookup_key},{rid_range},{named_range("Primary Inspection Year")})',                                           # Q: Primary Year
        f'=HYPERLINK(XLOOKUP({lookup_key},{rid_range},{named_range("Secondary School Link")}),XLOOKUP({lookup_key},{rid_range},{named_range("Secondary School")}))',  # R: Secondary School
        f'=LET(v,XLOOKUP({lookup_key},{rid_range},{named_range("Secondary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',      # S: Secondary Walk
        f'=XLOOKUP({lookup_key},{rid_range},{named_range("Secondary Ofsted")}     )',                                             # T: Secondary Ofsted
        f'=XLOOKUP({lookup_key},{rid_range},{named_range("Secondary Inspection Year")})',                                          # U: Secondary Year
        f'=LET(v,XLOOKUP({lookup_key},{rid_range},{named_range("Secondary Bus (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',        # V: Bus Time
        f'=XLOOKUP({lookup_key},{rid_range},{named_range("Secondary Bus Route")}  )',                                             # W: Bus Route
        "",  # X: Group Notes / WhatsApp
        "",  # Y: Ashby comments
        "",  # Z: Status
        "",  # AA: Status Reason
    ]

    last_col = col_letter(len(formulas) - 1)
    ws_view.update(
        range_name=f"A2:{last_col}2",
        values=[formulas],
        value_input_option="USER_ENTERED",
    )
    print(f"Column count: Data={len(COLUMN_HEADERS)}, View={len(formulas)}")
    print("Done!")


if __name__ == "__main__":
    main()
