"""Named range management across Data, View, and Constants tabs.

``named_range_name`` and ``_const_range_name`` generate deterministic range names.
``ensure_named_ranges`` syncs all ranges with the spreadsheet.
``ensure_constants_tab`` creates the Constants tab if missing.
"""

from __future__ import annotations

import logging
import re

import gspread

from houses.sheets.row import CONSTANTS_TAB, DATA_TAB, Row

logger = logging.getLogger(__name__)

# Headers used in the Constants tab (display name in col A, value in col B)
CONSTANTS_HEADERS: list[str] = [
    "Constant",
    "Value",
]

# (label, value_or_formula) pairs. Label is used for Const_ named range generation,
# value_or_formula is written to column B (USER_ENTERED so formulas are evaluated).
CONSTANTS_VALUES: list[tuple[str, str]] = [
    ("Current Sale Price (£)", "0"),
    ("Outstanding Mortgage (£)", "0"),
    ("Deposit", "=B2-B3"),
    ("Gross Ashby Contribution (£)", "0"),
    ("Mortgage Rate", "0.0495"),
    ("Mortgage Term (years)", "27"),
    ("Life Insurance Monthly (£)", "0"),
    ("Sinking Fund Rate", "0.01"),
    ("Rental Income (£)", "0"),
]


def named_range_name(header: str) -> str:
    """Generate a deterministic named range identifier from a column header.

    Strips special characters, CamelCases each word, prefixes with 'Data_'.
    E.g. 'Simon London (min)' → 'Data_SimonLondonMin'
    """
    clean = re.sub(r"[^a-zA-Z0-9 ]+", "", header).strip()
    words = clean.split()
    return "Data_" + "".join(w.capitalize() for w in words)


def _const_range_name(header: str) -> str:
    """Generate a Const_ named range from a constant name."""
    clean = re.sub(r"[^a-zA-Z0-9 ]+", "", header).strip()
    words = clean.split()
    return "Const_" + "".join(w.capitalize() for w in words)


def ensure_constants_tab(spreadsheet: gspread.Spreadsheet) -> gspread.Worksheet:
    """Create the Constants tab if missing. Never overwrites existing values."""
    try:
        ws = spreadsheet.worksheet(CONSTANTS_TAB)
        logger.info("'%s' tab already exists — leaving untouched", CONSTANTS_TAB)
        return ws
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=CONSTANTS_TAB, rows=20, cols=2)

    values = [CONSTANTS_HEADERS]
    for label, val in CONSTANTS_VALUES:
        values.append([label, val])
    ws.update(range_name="A1", values=values, value_input_option="USER_ENTERED")
    logger.info("Created '%s' tab with %d constants", CONSTANTS_TAB, len(CONSTANTS_VALUES))
    return ws


def ensure_named_ranges(spreadsheet: gspread.Spreadsheet) -> None:
    """Create or update all named ranges for Data, View, and Constants tabs.

    Cleans up orphaned ranges that are no longer generated.
    """
    existing = {r["name"]: r for r in (spreadsheet.list_named_ranges() or [])}
    current_names = set()

    requests = []

    # Data tab named ranges
    ws_data = spreadsheet.worksheet(DATA_TAB)
    sid_data = ws_data._properties["sheetId"]
    for col_idx, header in enumerate(Row.HEADERS):
        name = named_range_name(header)
        current_names.add(name)
        range_spec = {"sheetId": sid_data, "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1}
        if name in existing:
            rid = existing[name]["namedRangeId"]
            requests.append(
                {
                    "updateNamedRange": {
                        "namedRange": {"namedRangeId": rid, "name": name, "range": range_spec},
                        "fields": "range",
                    }
                }
            )
        else:
            requests.append({"addNamedRange": {"namedRange": {"name": name, "range": range_spec}}})

    # View tab named ranges
    ws_view = spreadsheet.worksheet("Properties View")
    sid_view = ws_view._properties["sheetId"]
    for name, col_idx in [
        ("View_RightmoveLink", 1),
        ("View_Map", 2),
        ("View_RightmoveID", 3),
        ("View_ListingAddress", 0),
        ("View_AshbyWorksEstimate", 34),
        ("View_DesignNeeded", 37),
        ("View_PlanningNeeded", 38),
        ("View_Status", 39),
    ]:
        current_names.add(name)
        range_spec = {"sheetId": sid_view, "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1}
        if name in existing:
            rid = existing[name]["namedRangeId"]
            requests.append(
                {
                    "updateNamedRange": {
                        "namedRange": {"namedRangeId": rid, "name": name, "range": range_spec},
                        "fields": "range",
                    }
                }
            )
        else:
            requests.append({"addNamedRange": {"namedRange": {"name": name, "range": range_spec}}})

    # Constants tab named ranges (single-cell ranges)
    ensure_constants_tab(spreadsheet)
    ws_const = spreadsheet.worksheet(CONSTANTS_TAB)
    sid_const = ws_const._properties["sheetId"]
    for row_idx, (label, _) in enumerate(CONSTANTS_VALUES):
        name = _const_range_name(label)
        current_names.add(name)
        range_spec = {
            "sheetId": sid_const,
            "startRowIndex": row_idx + 1,
            "endRowIndex": row_idx + 2,
            "startColumnIndex": 1,
            "endColumnIndex": 2,
        }
        if name in existing:
            rid = existing[name]["namedRangeId"]
            requests.append(
                {
                    "updateNamedRange": {
                        "namedRange": {"namedRangeId": rid, "name": name, "range": range_spec},
                        "fields": "range",
                    }
                }
            )
        else:
            requests.append({"addNamedRange": {"namedRange": {"name": name, "range": range_spec}}})

    # Delete orphaned ranges (names we no longer generate)
    for name, info in existing.items():
        prefixes = ("Data_", "View_", "Const_")
        if name.startswith(prefixes) and name not in current_names:
            requests.append({"deleteNamedRange": {"namedRangeId": info["namedRangeId"]}})

    if requests:
        spreadsheet.batch_update({"requests": requests})
