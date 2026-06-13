"""houses.sheets — Google Sheets integration for property data.

See the individual modules for implementation details:

* ``tab.py`` — ``Tab`` wrapper around gspread ``Worksheet``
* ``row.py`` — ``Row`` class for column schema, value formatting, and sheet writes
* ``formulas.py`` — View and Data tab formula definitions, ``sync_data_formulas``
* ``named_ranges.py`` — Named range management across all tabs
* ``rules.py`` — Generic conditional formatting rule primitives
* ``view.py`` — ``View`` class for View tab sync (formulas + formatting + rules)
* ``client.py`` — Google Sheets client lifecycle
"""

from __future__ import annotations

from houses.sheets.client import _real_get_client, get_client
from houses.sheets.formulas import (
    DATA_FORMULA_COLS,
    VIEW_FORMULA_COLS,
    VIEW_HEADERS,
    VIEW_MANUAL_COLUMNS,
    sync_data_formulas,
)
from houses.sheets.named_ranges import (
    CONSTANTS_VALUES,
    _const_range_name,
    ensure_constants_tab,
    ensure_named_ranges,
    named_range_name,
)
from houses.sheets.row import CONSTANTS_TAB, DATA_TAB, VIEW_TAB, Row, ensure_headers, write_enriched_row
from houses.sheets.tab import Tab
from houses.sheets.view import View, sync_view_formulas

# ── Backwards-compatible aliases ─────────────────────────────────────
# The Row class replaces several standalone functions. These aliases let
# existing callers use `from houses.sheets import col_index` unchanged.

COLUMN_HEADERS = Row.HEADERS
_USER_COLUMNS = Row._USER_COLUMNS
_FORMULA_COLUMNS = Row._FORMULA_COLUMNS

col_index = Row.index_of
col_letter = Row.letter_of
_build_full_row = Row.to_list
_rightmove_id = Row.rightmove_id
row_values = Row.from_property

# ── Public API ───────────────────────────────────────────────────────

__all__ = [
    # Classes
    "Tab",
    "Row",
    "View",
    # Tab name constants
    "DATA_TAB",
    "VIEW_TAB",
    "CONSTANTS_TAB",
    # Column schema
    "COLUMN_HEADERS",
    "_USER_COLUMNS",
    "_FORMULA_COLUMNS",
    "col_index",
    "col_letter",
    # Row operations
    "row_values",
    "_build_full_row",
    "_rightmove_id",
    "write_enriched_row",
    "ensure_headers",
    # View tab
    "VIEW_HEADERS",
    "VIEW_MANUAL_COLUMNS",
    "VIEW_FORMULA_COLS",
    "sync_view_formulas",
    # Data tab formulas
    "DATA_FORMULA_COLS",
    "sync_data_formulas",
    # Named ranges
    "named_range_name",
    "_const_range_name",
    "ensure_named_ranges",
    "ensure_constants_tab",
    "CONSTANTS_VALUES",
    # Client
    "get_client",
    "_real_get_client",
]
