"""One-time setup: create Properties Data and Properties View tabs with XLOOKUP formulas.

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
from houses.sheets import COLUMN_HEADERS  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

DATA_TAB = "Properties Data"
VIEW_TAB = "Properties View"

# View tab headers — these stay manual/fixed and are NOT in COLUMN_HEADERS
VIEW_HEADERS = [
    "Listing Address",
    "Rightmove Link",
    "Purchase Cost (£)",
    "EPC Rating",
    "Yearly Commute Total (£)",
    "Yearly Council Tax (£)",
    "Simon London (min)",
    "Lorena London (min)",
    "Bracknell Time (min)",
    "What the Area is Like",
    "Walk to Town (min)",
    "Walkable Amenities",
    "Primary School",
    "Primary Ofsted",
    "Primary Walk (min)",
    "Secondary School",
    "Secondary Ofsted",
    "Secondary Walk (min)",
    "Secondary Bus Route",
    "Group Notes / WhatsApp",
    "Ashby comments",
    "Status",
]


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
        print(f"Updating existing '{VIEW_TAB}' tab headers")
        ws_view.update(
            range_name=f"A1:{chr(64 + len(VIEW_HEADERS))}1",
            values=[VIEW_HEADERS],
            value_input_option="USER_ENTERED",
        )

    # Column B has the Rightmove URL, which matches column A in Properties Data
    # Time columns (H, I, J, M, P, S, T) show fractional hours = minutes/60
    # Manual columns (A, B, C, U, V, W) are left empty — user fills them in
    # Key: Rightmove ID (View col $C2) matched against Data col I ($I:$I)
    d = f"'{DATA_TAB}'"
    formulas = [
        "", "", "",                                                                              # A B C: manual
        f'=IFERROR(XLOOKUP($C2,{d}!$I:$I,{d}!$E:$E),"")',                                         # D: purchase cost (Data E)
        f'=IFERROR(XLOOKUP($C2,{d}!$I:$I,{d}!$AG:$AG),"")',                                       # E: EPC (Data AG)
        f'=IFERROR(LET(k,XLOOKUP($C2,{d}!$I:$I,{d}!$O:$O),g,XLOOKUP($C2,{d}!$I:$I,{d}!$K:$K),i,XLOOKUP($C2,{d}!$I:$I,{d}!$M:$M),IF(OR(k="",g="",i=""),"",46*(k+g+2*i))),"")',
        "",                                                                                         # G: (council tax removed)
        f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$I:$I,{d}!$J:$J),IF(v="","",IF(v*1=0,"",v/1440))),"")',  # H: Simon (Data J)
        f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$I:$I,{d}!$L:$L),IF(v="","",IF(v*1=0,"",v/1440))),"")',  # I: Lorena (Data L)
        f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$I:$I,{d}!$N:$N),IF(v="","",IF(v*1=0,"",v/1440))),"")',  # J: Bracknell (Data N)
        f'=IFERROR(XLOOKUP($C2,{d}!$I:$I,{d}!$AD:$AD),"")',                                       # K: area desc (Data AD)
        f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$I:$I,{d}!$AE:$AE),IF(v="","",IF(v*1=0,"",v/1440))),"")',# L: walk (Data AE)
        f'=IFERROR(XLOOKUP($C2,{d}!$I:$I,{d}!$AF:$AF),"")',                                       # M: amenities (Data AF)
        f'=IFERROR(HYPERLINK(XLOOKUP($C2,{d}!$I:$I,{d}!$S:$S),XLOOKUP($C2,{d}!$I:$I,{d}!$P:$P)),"")',  # N: primary (Data P,S)
        f'=IFERROR(XLOOKUP($C2,{d}!$I:$I,{d}!$T:$T),"")',                                         # O: primary ofsted (Data T)
        f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$I:$I,{d}!$R:$R),IF(v="","",IF(v*1=0,"",v/1440))),"")',  # P: primary walk (Data R)
        f'=IFERROR(HYPERLINK(XLOOKUP($C2,{d}!$I:$I,{d}!$Z:$Z),XLOOKUP($C2,{d}!$I:$I,{d}!$W:$W)),"")',  # Q: secondary (Data W,Z)
        f'=IFERROR(XLOOKUP($C2,{d}!$I:$I,{d}!$AA:$AA),"")',                                       # R: sec ofsted (Data AA)
        f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$I:$I,{d}!$Y:$Y),IF(v="","",IF(v*1=0,"",v/1440))),"")',  # S: sec walk (Data Y)
        f'=IFERROR(XLOOKUP($C2,{d}!$I:$I,{d}!$AI:$AI),"")',                                       # T: bus route (Data AI)
        "", "", "",  # U V W: manual
    ]
    from houses.sheets import col_letter
    last_col = col_letter(len(formulas) - 1)
    ws_view.update(
        range_name=f"A2:{last_col}2",
        values=[formulas],
        value_input_option="USER_ENTERED",
    )
    print(f"Column count: Data={len(COLUMN_HEADERS)}, View={len(VIEW_HEADERS)}")
    print("Done!")


if __name__ == "__main__":
    main()
