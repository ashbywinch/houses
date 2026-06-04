"""Sheet administration tool — column ops, state inspection, formula updates.

Usage:
    # Show current column layout
    uv run python scripts/sheet_tool.py layout

    # Move a column by header name to a new position
    uv run python scripts/sheet_tool.py move "Actual Postcode" --after "Approx Station Name"
    uv run python scripts/sheet_tool.py move "Actual Postcode" --after "Approx Station Name" --tab "Properties View"

    # Add a new column at the end
    uv run python scripts/sheet_tool.py add "New Column"

    # Rename a column header
    uv run python scripts/sheet_tool.py rename "Old Name" "New Name"

    # Show cell-level diff between two tabs for a given Rightmove ID
    uv run python scripts/sheet_tool.py diff "88375569" --tab "Properties Data" --other "Properties"

    # Delete a column by header name (safe: matches header text, not fragile index)
    uv run python scripts/sheet_tool.py delete "Obsolete Column"
    uv run python scripts/sheet_tool.py delete "Actual Postcode" --tab "Properties Data"

    # Delete a tab (cleans up named ranges first to avoid orphans)
    uv run python scripts/sheet_tool.py delete-tab "Properties View"

    # Update View tab formulas after column shifts
    uv run python scripts/sheet_tool.py refresh-formulas
"""

from __future__ import annotations

import json
import os
import sys

import gspread
from google.oauth2.service_account import Credentials

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from houses.config import settings  # noqa: E402
from houses.sheets import COLUMN_HEADERS, col_letter  # noqa: E402

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


def cmd_move(header: str, after: str | None, tab: str | None = None):
    creds = Credentials.from_service_account_info(
        json.loads(settings.service_account_json), scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(os.environ.get("HOUSES_SHEET_ID", settings.sheet_id))
    ws = sh.worksheet(tab or DATA_TAB)
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


def cmd_add(header: str, after: str | None = None):
    sh, ws = _get_sheet()
    sheet_id = ws._properties["sheetId"]
    headers = ws.get_all_values()[0]

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
            "insertDimension": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": dst_idx, "endIndex": dst_idx + 1},
            }
        }]
    }
    sh.batch_update(body)
    cl = col_letter(dst_idx)
    ws.update_acell(f"{cl}1", header)
    print(f"Added column '{header}' at position {dst_idx} ({cl})")


def _find_column(ws, header: str) -> int | None:
    """Return 0-based column index matching header name (case-insensitive), or None."""
    data = ws.get_all_values()
    for i, h in enumerate(data[0]):
        if h.strip().lower() == header.strip().lower():
            return i
    return None


def cmd_delete(header: str, tab: str | None = None):
    """Delete a column from the sheet by matching its header name.

    When --tab is omitted, searches both Properties Data and Properties View.
    If the header exists in both tabs, refuses with a message asking for --tab.
    If found in exactly one tab, deletes from that tab.

    Safe against index drift because it finds the column by header text first.
    """
    creds = Credentials.from_service_account_info(
        json.loads(settings.service_account_json), scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(os.environ.get("HOUSES_SHEET_ID", settings.sheet_id))

    tabs_to_check = [tab] if tab else [DATA_TAB, VIEW_TAB]

    found_in = {}
    for t in tabs_to_check:
        try:
            ws = sh.worksheet(t)
            idx = _find_column(ws, header)
            if idx is not None:
                found_in[t] = idx
        except Exception:
            pass

    if len(found_in) == 0:
        search_msg = f" in {tab}" if tab else f" in either '{DATA_TAB}' or '{VIEW_TAB}'"
        print(f"Column '{header}' not found{search_msg}")
        sys.exit(1)

    if len(found_in) > 1:
        tabs = "', '".join(found_in.keys())
        print(
            f"Column '{header}' exists in both '{tabs}'. "
            f"Specify --tab to disambiguate."
        )
        sys.exit(1)

    target_tab = next(iter(found_in))
    col_idx = found_in[target_tab]
    ws = sh.worksheet(target_tab)
    sheet_id = ws._properties["sheetId"]

    body = {
        "requests": [{
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1,
                }
            }
        }]
    }
    sh.batch_update(body)
    print(f"Deleted column '{header}' (col {col_idx}) from '{target_tab}'")


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
        _ = other_headers[i] if i < len(other_headers) else f"?{i}"
        other_val = ""
        if other_data and len(other_data) > 1 and i < len(other_data[1]):
            other_val = other_data[1][i].strip()
        if this_val != other_val:
            h = this_headers[i] if i < len(this_headers) else f"?{i}"
            print(f"  {col_letter(i):3s} {h:25s} {tab}={this_val[:40]:40s} {other_tab}={other_val[:40]}")


def cmd_delete_tab(tab: str):
    """Delete a worksheet tab, cleaning up its named ranges first to avoid orphans."""
    creds = Credentials.from_service_account_info(
        json.loads(settings.service_account_json), scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(os.environ.get("HOUSES_SHEET_ID", settings.sheet_id))
    ws = sh.worksheet(tab)
    sid = ws._properties["sheetId"]

    named_ranges = sh.list_named_ranges() or []
    cleanup = [{"deleteNamedRange": {"namedRangeId": r["namedRangeId"]}}
               for r in named_ranges
               if r.get("range", {}).get("sheetId") == sid]
    if cleanup:
        sh.batch_update({"requests": cleanup})
        print(f"Cleaned up {len(cleanup)} named ranges on '{tab}'")

    sh.del_worksheet(ws)
    print(f"Deleted tab '{tab}'")


def cmd_refresh_formulas():
    creds = Credentials.from_service_account_info(
        json.loads(settings.service_account_json), scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(os.environ.get("HOUSES_SHEET_ID", settings.sheet_id))
    from houses.sheets import sync_view_formulas
    sync_view_formulas(sh)
    print("View formulas refreshed via named ranges")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "layout":
        cmd_layout()
    elif cmd == "move":
        if len(sys.argv) < 3:
            print("Usage: sheet_tool.py move <header> [--after <header>] [--tab <name>]")
            return
        header = sys.argv[2]
        after = None
        tab = None
        if "--after" in sys.argv:
            after = sys.argv[sys.argv.index("--after") + 1]
        if "--tab" in sys.argv:
            tab = sys.argv[sys.argv.index("--tab") + 1]
        cmd_move(header, after, tab)
    elif cmd == "add":
        if len(sys.argv) < 3:
            print("Usage: sheet_tool.py add <header> [--after <header>]")
            return
        header = sys.argv[2]
        after = None
        if "--after" in sys.argv:
            after = sys.argv[sys.argv.index("--after") + 1]
        cmd_add(header, after)
    elif cmd == "delete" or cmd == "delete-column":
        if len(sys.argv) < 3:
            print("Usage: sheet_tool.py delete <header> [--tab <name>]")
            return
        header = sys.argv[2]
        tab = None
        if "--tab" in sys.argv:
            tab = sys.argv[sys.argv.index("--tab") + 1]
        cmd_delete(header, tab)
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
    elif cmd == "delete-tab":
        if len(sys.argv) < 3:
            print("Usage: sheet_tool.py delete-tab <tab_name>")
            return
        cmd_delete_tab(sys.argv[2])
    elif cmd == "refresh-formulas":
        cmd_refresh_formulas()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
