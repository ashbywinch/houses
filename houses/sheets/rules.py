"""Generic conditional-formatting rule-building helpers for Google Sheets.

These primitives know nothing about domain concepts (EPC, commute, Ofsted).
They build the Google Sheets API request structures for conditional formatting.
Domain-specific rules live in ``houses.sheet_presentation`` and call these.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Conditional formatting colors (RGB 0-1 floats for Google Sheets API)
GREEN_BG = {"red": 0.85, "green": 0.92, "blue": 0.83}
ORANGE_BG = {"red": 1.0, "green": 0.95, "blue": 0.80}
RED_BG = {"red": 0.96, "green": 0.80, "blue": 0.80}
GREY_TEXT = {"red": 0.6, "green": 0.6, "blue": 0.6}

# Public exports
__all__ = [
    "GREEN_BG",
    "ORANGE_BG",
    "RED_BG",
    "GREY_TEXT",
    "RuleContext",
    "TimeThresholds",
]


@dataclass(frozen=True)
class RuleContext:
    """The sheet target a conditional-formatting rule appends to.

    Bundles the request list, sheet id, header-to-column-index lookup, and the
    column-index-to-letter helper every rule builder needs.
    """

    fmt_requests: list
    sid: int
    header_lookup: dict
    col_letter_fn: Callable

    # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    def add(
        self,
        header_name: str,
        formula: str,
        bg_color: dict | None = None,
        text_color: dict | None = None,
    ) -> None:
        """Append a single conditional formatting rule to fmt_requests."""
        col_idx = self.header_lookup[header_name.lower()]
        rule = {
            "addConditionalFormatRule": {
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
                "rule": {
                    "ranges": [
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
                        {
                            "sheetId": self.sid,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                            "startRowIndex": 1,
                        }
                    ],
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
                    "booleanRule": {
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
                        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]},
                        "format": {},
                    },
                }
            }
        }
        if bg_color:
            rule["addConditionalFormatRule"]["rule"]["booleanRule"]["format"]["backgroundColor"] = bg_color
        if text_color:
            text_fmt = rule["addConditionalFormatRule"]["rule"]["booleanRule"]["format"]
            text_fmt["textFormat"] = {"foregroundColor": text_color}
        self.fmt_requests.append(rule)

    def time_tiered(self, header: str, thresholds: TimeThresholds) -> None:
        """Add green/orange/red for a time column: <G:H G:M green, G:H G:M–O:H O:M orange, >O:H O:M red."""
        letter = self.col_letter_fn(self.header_lookup[header])
        formula = f'=AND(${letter}2<>"",${letter}2<TIME({thresholds.green_hours},{thresholds.green_mins},0))'
        self.add(header, formula, GREEN_BG)
        orange_f = f'=AND(${letter}2<>"",${letter}2>=TIME({thresholds.green_hours},{thresholds.green_mins},0),${letter}2<=TIME({thresholds.orange_hours},{thresholds.orange_mins},0))'  # noqa: E501  # the Google Sheets formula must stay one physical line to remain a valid single f-string
        self.add(header, orange_f, ORANGE_BG)
        formula = f'=AND(${letter}2<>"",${letter}2>TIME({thresholds.orange_hours},{thresholds.orange_mins},0))'
        self.add(header, formula, RED_BG)

    def numeric_tiered(self, header: str, green_max: float, orange_max: float) -> None:
        """Add green/orange/red for a numeric column: <green_max green, green_max–orange_max orange, >orange_max red."""
        letter = self.col_letter_fn(self.header_lookup[header])
        self.add(header, f'=AND(${letter}2<>"",${letter}2<{green_max})', GREEN_BG)
        orange_f = f'=AND(${letter}2<>"",${letter}2>={green_max},${letter}2<={orange_max})'
        self.add(header, orange_f, ORANGE_BG)
        self.add(header, f'=AND(${letter}2<>"",${letter}2>{orange_max})', RED_BG)


@dataclass(frozen=True)
class TimeThresholds:
    """Green/orange tier boundaries for a time column, as TIME() arguments."""

    green_hours: int
    green_mins: int
    orange_hours: int
    orange_mins: int

