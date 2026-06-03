"""Sheet administration tool — column ops, state inspection, formula updates.

Usage:
    # Show current column layout
    uv run python scripts/sheet_tool.py layout

    # Move a column by header name to a new position
    uv run python scripts/sheet_tool.py move "Actual Postcode" --after "Approx Station Name"

    # Add a new column at the end
    uv run python scripts/sheet_tool.py add "New Column"

    # Rename a column header
    uv run python scripts/sheet_tool.py rename "Old Name" "New Name"

    # Show cell-level diff between two tabs for a given Rightmove ID
    uv run python scripts/sheet_tool.py diff "88375569" --tab "Properties Data" --other "Properties"

    # Update View tab formulas after column shifts
    uv run python scripts/sheet_tool.py refresh-formulas
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from houses.config import settings  # noqa: E402
from houses.sheets import col_letter, col_index, COLUMN_HEADERS  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DATA_TAB = "Properties Data"
VIEW_TAB = "Properties View"


def _get_sheet() -> tuple[gspread.Spreadsheet, gspread.Worksheet]:
    creds = Credentials.from_service_account_info(
        json.loads(settings.service_account_json), scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(os.environ.get("HOUSES_SHEET_ID", settings.sheet_id))
    ws = sh.worksheet(DATA_TAB)
    return sh, ws


def cmd_layout():
    sh, ws = _get_sheet()
    data = ws.get_all_values()
    print(f"{DATA_TAB}: {len(data)} rows, {len(data[0])} cols")
    for i in range(max(len(COLUMN_HEADERS), len(data[0]))):
        code_name = COLUMN_HEADERS[i] if i < len(COLUMN_HEADERS) else "(no code header)"
        sheet_name = data[0][i] if i < len(data[0]) else "(no sheet header)"
        mismatch = " ← MISMATCH" if code_name != sheet_name else ""
        print(f"  {col_letter(i):3s} ({i:2d}) {sheet_name:30s}{mismatch}")
        if mismatch:
            print(f"       code: {code_name}")


def cmd_move(header: str, after: str | None):
    sh, ws = _get_sheet()
    sheet_id = ws._properties["sheetId"]

    data = ws.get_all_values()
    headers = data[0]
    src_idx = None
    for i, h in enumerate(headers):
        if h.strip().lower() == header.strip().lower():
            src_idx = i
            break
    if src_idx is None:
        print(f"Column '{header}' not found in sheet")
        sys.exit(1)

    if after:
        dst_idx = None
        for i, h in enumerate(headers):
            if h.strip().lower() == after.strip().lower():
                dst_idx = i + 1
                break
        if dst_idx is None:
            print(f"Column '{after}' not found in sheet")
            sys.exit(1)
    else:
        dst_idx = len(headers)

    body = {
        "requests": [{
            "moveDimension": {
                "source": {"sheetId": sheet_id, "dimension": "COLUMNS",
                           "startIndex": src_idx, "endIndex": src_idx + 1},
                "destinationIndex": dst_idx,
            }
        }]
    }
    sh.batch_update(body)
    print(f"Moved '{header}' to position {dst_idx}")


def cmd_add(header: str):
    sh, ws = _get_sheet()
    sheet_id = ws._properties["sheetId"]
    col_count = ws.col_count

    body = {
        "requests": [{
            "insertDimension": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": col_count, "endIndex": col_count + 1},
            }
        }]
    }
    sh.batch_update(body)
    ws.update_acell(f"{col_letter(col_count)}1", header)
    print(f"Added column '{header}' at position {col_count}")


def cmd_rename(old_name: str, new_name: str):
    sh, ws = _get_sheet()
    headers = ws.get_all_values()[0]
    for i, h in enumerate(headers):
        if h.strip().lower() == old_name.strip().lower():
            ws.update_acell(f"{col_letter(i)}1", new_name)
            print(f"Renamed '{old_name}' → '{new_name}'")
            return
    print(f"Column '{old_name}' not found")


def cmd_diff(rid: str, tab: str, other: str | None):
    sh, ws = _get_sheet()
    this_data = ws.get_all_values()
    this_headers = this_data[0]

    # Find row by Rightmove ID
    rid_col = None
    for i, h in enumerate(this_headers):
        if h.strip().lower() == "rightmove id":
            rid_col = i
            break
    if rid_col is None:
        print("No Rightmove ID column found")
        return

    this_row = None
    this_row_num = None
    for i, r in enumerate(this_data[1:], 2):
        if len(r) > rid_col and r[rid_col].strip() == rid:
            this_row = r
            this_row_num = i
            break

    if this_row is None:
        print(f"Row with Rightmove ID '{rid}' not found in {tab}")
        return

    other_tab = other or tab
    try:
        ws2 = sh.worksheet(other_tab)
    except Exception as e:
        print(f"Could not open tab '{other_tab}': {e}")
        return

    other_data = ws2.get_all_values()
    other_headers = other_data[0]

    print(f"Diff for Rightmove ID {rid} ({tab} row {this_row_num} vs {other_tab}):")
    for i in range(min(len(this_row), len(other_headers))):
        this_val = this_row[i].strip() if i < len(this_row) else ""
        other_header = other_headers[i] if i < len(other_headers) else f"?{i}"
        other_val = ""
        if other_data and len(other_data) > 1 and i < len(other_data[1]):
            other_val = other_data[1][i].strip()
        if this_val != other_val:
            h = this_headers[i] if i < len(this_headers) else f"?{i}"
            print(f"  {col_letter(i):3s} {h:25s} {tab}={this_val[:40]:40s} {other_tab}={other_val[:40]}")


def cmd_refresh_formulas():
    """Rewrite View tab XLOOKUP formulas to use Rightmove ID (Data column H)."""
    from houses.sheets import col_letter

    creds = Credentials.from_service_account_info(
        json.loads(settings.service_account_json), scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(os.environ.get("HOUSES_SHEET_ID", settings.sheet_id))
    ws = sh.worksheet(VIEW_TAB)
    data = ws.get_all_values()
    num_rows = len(data)
    d = f"'{DATA_TAB}'"

    formulas = {
        'D': f'=IFERROR(XLOOKUP($C2,{d}!$H:$H,{d}!$E:$E),"")',
        'E': f'=IFERROR(XLOOKUP($C2,{d}!$H:$H,{d}!$AF:$AF),"")',
        'F': f'=IFERROR(LET(k,XLOOKUP($C2,{d}!$H:$H,{d}!$N:$N),g,XLOOKUP($C2,{d}!$H:$H,{d}!$J:$J),i,XLOOKUP($C2,{d}!$H:$H,{d}!$L:$L),IF(OR(k="",g="",i=""),"",46*(k+g+2*i))),"")',
        'H': f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$H:$H,{d}!$I:$I),IF(v="","",IF(v*1=0,"",v/1440))),"")',
        'I': f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$H:$H,{d}!$K:$K),IF(v="","",IF(v*1=0,"",v/1440))),"")',
        'J': f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$H:$H,{d}!$M:$M),IF(v="","",IF(v*1=0,"",v/1440))),"")',
        'K': f'=IFERROR(XLOOKUP($C2,{d}!$H:$H,{d}!$AC:$AC),"")',
        'L': f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$H:$H,{d}!$AD:$AD),IF(v="","",IF(v*1=0,"",v/1440))),"")',
        'M': f'=IFERROR(XLOOKUP($C2,{d}!$H:$H,{d}!$AE:$AE),"")',
        'N': f'=IFERROR(HYPERLINK(XLOOKUP($C2,{d}!$H:$H,{d}!$R:$R),XLOOKUP($C2,{d}!$H:$H,{d}!$O:$O)),"")',
        'O': f'=IFERROR(XLOOKUP($C2,{d}!$H:$H,{d}!$S:$S),"")',
        'P': f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$H:$H,{d}!$Q:$Q),IF(v="","",IF(v*1=0,"",v/1440))),"")',
        'Q': f'=IFERROR(HYPERLINK(XLOOKUP($C2,{d}!$H:$H,{d}!$Y:$Y),XLOOKUP($C2,{d}!$H:$H,{d}!$V:$V)),"")',
        'R': f'=IFERROR(XLOOKUP($C2,{d}!$H:$H,{d}!$Z:$Z),"")',
        'S': f'=IFERROR(LET(v,XLOOKUP($C2,{d}!$H:$H,{d}!$X:$X),IF(v="","",IF(v*1=0,"",v/1440))),"")',
        'T': f'=IFERROR(XLOOKUP($C2,{d}!$H:$H,{d}!$AH:$AH),"")',
        'X': f'=IFERROR(XLOOKUP($C2,{d}!$H:$H,{d}!$T:$T),"")',
        'Y': f'=IFERROR(XLOOKUP($C2,{d}!$H:$H,{d}!$U:$U),"")',
        'Z': f'=IFERROR(XLOOKUP($C2,{d}!$H:$H,{d}!$AA:$AA),"")',
        'AA': f'=IFERROR(XLOOKUP($C2,{d}!$H:$H,{d}!$AB:$AB),"")',
    }

    for col_let, formula in formulas.items():
        if num_rows > 1:
            f_list = [[formula] for _ in range(num_rows - 1)]
            ws.update(values=f_list, range_name=f'{col_let}2:{col_let}{num_rows}',
                       value_input_option='USER_ENTERED')

    print(f"Updated {len(formulas)} View formulas (key: Data H)")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "layout":
        cmd_layout()
    elif cmd == "move":
        if len(sys.argv) < 3:
            print("Usage: sheet_tool.py move <header> [--after <header>]")
            return
        header = sys.argv[2]
        after = None
        if "--after" in sys.argv:
            after = sys.argv[sys.argv.index("--after") + 1]
        cmd_move(header, after)
    elif cmd == "add":
        if len(sys.argv) < 3:
            print("Usage: sheet_tool.py add <header>")
            return
        cmd_add(sys.argv[2])
    elif cmd == "rename":
        if len(sys.argv) < 4:
            print("Usage: sheet_tool.py rename <old> <new>")
            return
        cmd_rename(sys.argv[2], sys.argv[3])
    elif cmd == "diff":
        if len(sys.argv) < 3:
            print("Usage: sheet_tool.py diff <rightmove_id> [--tab <name>] [--other <name>]")
            return
        tab = DATA_TAB
        other = None
        if "--tab" in sys.argv:
            tab = sys.argv[sys.argv.index("--tab") + 1]
        if "--other" in sys.argv:
            other = sys.argv[sys.argv.index("--other") + 1]
        cmd_diff(sys.argv[2], tab, other)
    elif cmd == "refresh-formulas":
        cmd_refresh_formulas()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
