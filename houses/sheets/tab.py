"""Tab — wraps a gspread Worksheet, auto-qualifying ranges with the sheet name.

Every cell write goes through ``batch_update``, which prefixes bare ranges
with ``'SheetName'!`` so Google Sheets never defaults to the wrong tab.
Use ``Tab`` everywhere instead of raw ``Worksheet``.
"""

from __future__ import annotations

from typing import Any

import gspread


class Tab:
    """Wraps a gspread Worksheet, auto-qualifying all ranges with the sheet name.

    Every cell write goes through ``batch_update``, which prefixes bare
    ranges with ``'SheetName'!`` so Google Sheets never defaults to the
    wrong tab. Use ``Tab`` everywhere instead of raw ``Worksheet``.
    """

    def __init__(self, ws: gspread.Worksheet):
        self._ws = ws

    @property
    def title(self) -> str:
        return self._ws.title

    @property
    def id(self) -> int:
        return self._ws.id

    @property
    def row_count(self) -> int:
        return self._ws.row_count

    @property
    def spreadsheet(self) -> gspread.Spreadsheet:
        return self._ws.spreadsheet

    def get_all_values(self) -> list[list[str]]:
        return self._ws.get_all_values()

    def append_row(self, values: list[str], value_input_option: str = "USER_ENTERED") -> None:
        self._ws.append_row(values, value_input_option=value_input_option)

    def update_cell(self, row: int, col: int, value: str) -> None:
        self._ws.update_cell(row, col, value)

    def _qualify(self, cells: list[dict[str, Any]]) -> None:
        """Prefix bare ranges with the sheet name in-place."""
        for cell in cells:
            rng = cell.get("range", "")
            if rng and not rng.startswith("'"):
                cell["range"] = f"'{self._ws.title}'!{rng}"

    def batch_update(self, cells: list[dict[str, Any]]) -> None:
        self._qualify(cells)
        self._ws.spreadsheet.values_batch_update(
            {"valueInputOption": "USER_ENTERED", "data": cells},
        )
