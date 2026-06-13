"""Generic conditional-formatting rule-building helpers for Google Sheets.

These primitives know nothing about domain concepts (EPC, commute, Ofsted).
They build the Google Sheets API request structures for conditional formatting.
Domain-specific rules live in ``houses.sheet_presentation`` and call these.
"""

from __future__ import annotations

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
    "add_rule",
    "add_time_tiered",
    "add_numeric_tiered",
]


def add_rule(
    fmt_requests: list,
    sid: int,
    header_lookup: dict,
    col_letter_fn,
    header_name: str,
    formula: str,
    bg_color: dict | None = None,
    text_color: dict | None = None,
) -> None:
    """Append a single conditional formatting rule to fmt_requests."""
    col_idx = header_lookup[header_name.lower()]
    rule = {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [
                    {"sheetId": sid, "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1, "startRowIndex": 1}
                ],
                "booleanRule": {
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
    fmt_requests.append(rule)


def add_time_tiered(
    fmt_requests: list,
    sid: int,
    header_lookup: dict,
    col_letter_fn,
    header: str,
    green_hours: int,
    green_mins: int,
    orange_hours: int,
    orange_mins: int,
) -> None:
    """Add green/orange/red for a time column: <G:H G:M green, G:H G:M–O:H O:M orange, >O:H O:M red."""
    letter = col_letter_fn(header_lookup[header])
    add_rule(
        fmt_requests,
        sid,
        header_lookup,
        col_letter_fn,
        header,
        f'=AND(${letter}2<>"",${letter}2<TIME({green_hours},{green_mins},0))',
        GREEN_BG,
    )
    orange_f = f'=AND(${letter}2<>"",${letter}2>=TIME({green_hours},{green_mins},0),${letter}2<=TIME({orange_hours},{orange_mins},0))'  # noqa: E501
    add_rule(fmt_requests, sid, header_lookup, col_letter_fn, header, orange_f, ORANGE_BG)
    add_rule(
        fmt_requests,
        sid,
        header_lookup,
        col_letter_fn,
        header,
        f'=AND(${letter}2<>"",${letter}2>TIME({orange_hours},{orange_mins},0))',
        RED_BG,
    )


def add_numeric_tiered(
    fmt_requests: list,
    sid: int,
    header_lookup: dict,
    col_letter_fn,
    header: str,
    green_max: float,
    orange_max: float,
) -> None:
    """Add green/orange/red for a numeric column: <green_max green, green_max–orange_max orange, >orange_max red."""
    letter = col_letter_fn(header_lookup[header])
    add_rule(
        fmt_requests,
        sid,
        header_lookup,
        col_letter_fn,
        header,
        f'=AND(${letter}2<>"",${letter}2<{green_max})',
        GREEN_BG,
    )
    orange_f = f'=AND(${letter}2<>"",${letter}2>={green_max},${letter}2<={orange_max})'
    add_rule(fmt_requests, sid, header_lookup, col_letter_fn, header, orange_f, ORANGE_BG)
    add_rule(
        fmt_requests, sid, header_lookup, col_letter_fn, header, f'=AND(${letter}2<>"",${letter}2>{orange_max})', RED_BG
    )
