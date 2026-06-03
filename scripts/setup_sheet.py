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

    K = f"VALUE(INDEX(View_RightmoveID, ROW()))"
    L = "INDEX(View_RightmoveLink, ROW())"
    NR = named_range_name
    RID = NR("Rightmove ID")

    formulas = [
        "",  # A: Listing Address (manual)
        "",  # B: Rightmove Link (manual)
        f'=REGEXEXTRACT({L},"properties/(\\d+)")',                                                     # C: ID from URL
        f'=XLOOKUP({K},{RID},{NR("Price (£)")}    )',                                                   # D
        f'=XLOOKUP({K},{RID},{NR("EPC Rating")}    )',                                                  # E
        f'=LET(k,XLOOKUP({K},{RID},{NR("Bracknell Cost (£)")}),g,XLOOKUP({K},{RID},{NR("Simon London Cost (£)")}),i,XLOOKUP({K},{RID},{NR("Lorena London Cost (£)")}),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)))',  # F
        "",                                                                                               # G
        f'=LET(v,XLOOKUP({K},{RID},{NR("Simon London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',        # H
        f'=LET(v,XLOOKUP({K},{RID},{NR("Lorena London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',       # I
        f'=LET(v,XLOOKUP({K},{RID},{NR("Bracknell Time (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',      # J
        f'=XLOOKUP({K},{RID},{NR("Area Description")}     )',                                             # K
        f'=LET(v,XLOOKUP({K},{RID},{NR("Walk to Town (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',        # L
        f'=XLOOKUP({K},{RID},{NR("Walkable Amenities")}   )',                                             # M
        f'=HYPERLINK(XLOOKUP({K},{RID},{NR("Primary School Link")}),XLOOKUP({K},{RID},{NR("Primary School")}))',  # N
        f'=XLOOKUP({K},{RID},{NR("Primary Ofsted")}       )',                                             # O
        f'=LET(v,XLOOKUP({K},{RID},{NR("Primary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',        # P
        f'=HYPERLINK(XLOOKUP({K},{RID},{NR("Secondary School Link")}),XLOOKUP({K},{RID},{NR("Secondary School")}))',  # Q
        f'=XLOOKUP({K},{RID},{NR("Secondary Ofsted")}     )',                                             # R
        f'=LET(v,XLOOKUP({K},{RID},{NR("Secondary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',      # S
        f'=XLOOKUP({K},{RID},{NR("Secondary Bus Route")}  )',                                             # T
        "", "", "",  # U V W: manual (Notes, Comments, Status)
        f'=XLOOKUP({K},{RID},{NR("Primary Inspection Year")})',                                           # X
        "",  # Y: Primary Inspection Summary (removed from Data)
        f'=XLOOKUP({K},{RID},{NR("Secondary Inspection Year")})',                                          # Z
        "",  # AA: Secondary Inspection Summary (removed from Data)
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
