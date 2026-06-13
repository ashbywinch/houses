"""View — encapsulates all View tab operations: formulas, formatting, rules, and column groups.

Usage::

    view = View(spreadsheet)
    view.sync()
"""

from __future__ import annotations

import json
import logging

import gspread

from houses.sheet_presentation import apply_color_rules, apply_data_validations
from houses.sheets.formulas import VIEW_FORMULA_COLS
from houses.sheets.named_ranges import ensure_named_ranges
from houses.sheets.row import Row

logger = logging.getLogger(__name__)

# Conditional formatting colors (RGB 0-1 floats for Google Sheets API)
GREY_TEXT = {"red": 0.6, "green": 0.6, "blue": 0.6}

VIEW_TAB = "Properties View"


class View:
    """Manages the View tab: formulas, cell formatting, conditional rules, and column layout."""

    def __init__(self, spreadsheet: gspread.Spreadsheet) -> None:
        self._spreadsheet = spreadsheet
        self._ws = spreadsheet.worksheet(VIEW_TAB)
        self._sid = self._ws._properties["sheetId"]
        self._headers: list[str] = []
        self._header_lookup: dict[str, int] = {}
        self._num_rows = 0

    # ── Public API ──────────────────────────────────────────────────

    def sync(self) -> None:
        """Full View tab sync: named ranges → formulas → formatting → rules → groups.

        This is the single source of truth for View formula generation — called by
        both the production refresh-formulas command and integration tests.
        """
        ensure_named_ranges(self._spreadsheet)

        data = self._ws.get_all_values()
        self._num_rows = len(data)
        self._headers = data[0] if data else []
        self._header_lookup = {h.strip().lower(): i for i, h in enumerate(self._headers)}

        self._write_formulas()
        self._apply_cell_formats()
        self._apply_conditional_formatting()
        self._apply_column_groups()

    # ── View formula syncing ────────────────────────────────────────

    def _write_formulas(self) -> None:
        """Write View tab formulas from VIEW_FORMULA_COLS."""
        for header_key, formula in VIEW_FORMULA_COLS.items():
            if header_key in self._header_lookup:
                cl = Row.letter_of(self._header_lookup[header_key])
                if self._num_rows > 1:
                    write_rows = max(self._num_rows - 1, 1)
                    self._ws.update(
                        values=[[formula] for _ in range(write_rows)],
                        range_name=f"{cl}2:{cl}{1 + write_rows}",
                        value_input_option="USER_ENTEred",
                    )

    # ── Cell formatting ─────────────────────────────────────────────

    def _apply_cell_formats(self) -> None:
        """Apply number formats, text wrap, frozen rows, and header styling."""
        fmt_requests: list[dict] = []

        # Time format for duration columns
        time_cols = [
            "simon london",
            "lorena london",
            "bracknell time",
            "walk to town",
            "primary walk",
            "secondary walk",
            "secondary bus",
        ]
        for h in time_cols:
            if h in self._header_lookup:
                ci = self._header_lookup[h]
                fmt_requests.append(
                    {
                        "repeatCell": {
                            "range": {"sheetId": self._sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                            "cell": {"userEnteredFormat": {"numberFormat": {"type": "TIME", "pattern": "[h]:mm"}}},
                            "fields": "userEnteredFormat.numberFormat",
                        }
                    }
                )

        # Currency format for cost columns
        currency_cols = [
            "purchase cost (£)",
            "monthly mortgage payment (£)",
            "monthly sinking fund (£)",
            "monthly life insurance (£)",
            "monthly commute cost (£)",
            "monthly council tax (£)",
            "total monthly housing cost (£)",
            "ashby works estimate (£)",
        ]
        for h in currency_cols:
            if h in self._header_lookup:
                ci = self._header_lookup[h]
                fmt_requests.append(
                    {
                        "repeatCell": {
                            "range": {"sheetId": self._sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                            "cell": {
                                "userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "£#,##0.00"}}
                            },
                            "fields": "userEnteredFormat.numberFormat",
                        }
                    }
                )

        # Grey text for Monthly Life Insurance (constant, visually distinct)
        life_key = "monthly life insurance (£)"
        if life_key in self._header_lookup:
            ci = self._header_lookup[life_key]
            fmt_requests.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": self._sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"foregroundColor": GREY_TEXT}}},
                        "fields": "userEnteredFormat.textFormat",
                    }
                }
            )

        # Bold for Total Monthly Housing Cost
        total_key = "total monthly housing cost (£)"
        if total_key in self._header_lookup:
            ci = self._header_lookup[total_key]
            fmt_requests.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": self._sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat",
                    }
                }
            )

        # Text wrap for description columns
        wrap_cols = [
            "what the area is like",
            "walkable amenities",
            "simon london route",
            "lorena london route",
            "primary school",
            "secondary school",
            "group notes / whatsapp",
            "ashby comments",
            "ashby works estimate (£)",
            "status reason",
            "primary inspection year",
            "secondary inspection year",
            "secondary bus",
        ]
        for h in wrap_cols:
            if h in self._header_lookup:
                ci = self._header_lookup[h]
                fmt_requests.append(
                    {
                        "repeatCell": {
                            "range": {"sheetId": self._sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                            "fields": "userEnteredFormat.wrapStrategy",
                        }
                    }
                )

        # Frozen rows and columns
        fmt_requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": self._sid,
                        "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 4},
                    },
                    "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
                }
            }
        )

        # Top-align all cells
        fmt_requests.append(
            {
                "repeatCell": {
                    "range": {"sheetId": self._sid},
                    "cell": {"userEnteredFormat": {"verticalAlignment": "TOP"}},
                    "fields": "userEnteredFormat.verticalAlignment",
                }
            }
        )

        # Header row style (dark bg, white bold text)
        fmt_requests.append(
            {
                "repeatCell": {
                    "range": {"sheetId": self._sid, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.18, "green": 0.24, "blue": 0.31},
                            "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            }
        )

        if fmt_requests:
            self._spreadsheet.batch_update({"requests": fmt_requests})

    # ── Conditional formatting ──────────────────────────────────────

    def _apply_conditional_formatting(self) -> None:
        """Clear existing conditional formats and re-apply domain-specific rules."""
        extra_requests: list[dict] = []

        # Clear existing conditional formatting rules for the View tab
        # Must delete from highest index to lowest since batch processes in order
        try:
            sheet_data = self._spreadsheet.client.request(
                "get",
                f"https://sheets.googleapis.com/v4/spreadsheets/{self._spreadsheet.id}",
                params={"fields": "sheets(conditionalFormats,properties.sheetId)"},
            )
            parsed = json.loads(sheet_data.content)
            for s in parsed.get("sheets", []):
                if s["properties"]["sheetId"] == self._sid:
                    rule_count = len(s.get("conditionalFormats", []))
                    for i in range(rule_count - 1, -1, -1):
                        extra_requests.append({"deleteConditionalFormatRule": {"sheetId": self._sid, "index": i}})
                    break
        except Exception as exc:
            logger.warning("Failed to clear existing conditional formatting rules: %s", exc)

        apply_color_rules(extra_requests, self._sid, self._headers, Row.letter_of)
        apply_data_validations(extra_requests, self._sid, self._headers)

        if extra_requests:
            self._spreadsheet.batch_update({"requests": extra_requests})

    # ── Column groups and borders ───────────────────────────────────

    def _apply_column_groups(self) -> None:
        """Apply visual zone separators, column grouping, and gap column widths."""
        requests: list[dict] = []

        # Visual zone separators — thick right borders between column groups
        zone_boundaries = [5, 14, 25, 32]  # last column index of each zone (pre-gap)
        for col in zone_boundaries:
            requests.append(
                {
                    "updateBorders": {
                        "range": {
                            "sheetId": self._sid,
                            "startColumnIndex": col,
                            "endColumnIndex": col + 1,
                        },
                        "right": {
                            "style": "SOLID_MEDIUM",
                            "color": {"red": 0.5, "green": 0.5, "blue": 0.5},
                        },
                    }
                }
            )

        # Delete existing column groups first to avoid accumulation
        try:
            existing_sheet = json.loads(
                self._spreadsheet.client.request(
                    "get",
                    f"https://sheets.googleapis.com/v4/spreadsheets/{self._spreadsheet.id}",
                    params={"fields": "sheets(properties,columnGroups)"},
                ).content
            )
            for s in existing_sheet.get("sheets", []):
                if s["properties"]["sheetId"] == self._sid:
                    for cg in sorted(s.get("columnGroups", []), key=lambda x: x.get("depth", 0), reverse=True):
                        r = cg["range"]
                        requests.append(
                            {
                                "deleteDimensionGroup": {
                                    "range": {
                                        "sheetId": self._sid,
                                        "dimension": "COLUMNS",
                                        "startIndex": r["startIndex"],
                                        "endIndex": r["endIndex"],
                                    }
                                }
                            }
                        )
                    break
        except Exception as exc:
            logger.warning("Failed to clear column groups: %s", exc)

        if requests:
            self._spreadsheet.batch_update({"requests": requests})
            requests.clear()

        # Add column groups
        zones = [
            (0, 6),
            (7, 15),
            (16, 26),
            (27, 33),
            (34, 41),
        ]
        for start, end in zones:
            requests.append(
                {
                    "addDimensionGroup": {
                        "range": {
                            "sheetId": self._sid,
                            "dimension": "COLUMNS",
                            "startIndex": start,
                            "endIndex": end,
                        }
                    }
                }
            )

        # Gap columns: very narrow width
        gap_cols = {6, 15, 26, 33}
        for gc in gap_cols:
            requests.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": self._sid,
                            "dimension": "COLUMNS",
                            "startIndex": gc,
                            "endIndex": gc + 1,
                        },
                        "properties": {"pixelSize": 16},
                        "fields": "pixelSize",
                    }
                }
            )

        if requests:
            self._spreadsheet.batch_update({"requests": requests})


def sync_view_formulas(spreadsheet: gspread.Spreadsheet) -> None:
    """Backwards-compatible wrapper — creates a View and runs full sync.

    Equivalent to ``View(spreadsheet).sync()``. Kept so existing callers
    can use ``from houses.sheets import sync_view_formulas`` unchanged.
    """
    View(spreadsheet).sync()
