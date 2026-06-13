"""Domain-specific sheet presentation rules — conditional formatting and data validations.

These functions encode domain knowledge about what values are "good" vs "bad"
for EPC ratings, commute times, Ofsted grades, etc. They call generic rule
helpers from ``houses.sheets.rules``.

This module lives outside the ``houses.sheets`` package because it depends
on domain concepts (EPC bands, commute thresholds, Ofsted grades). The sheet
infrastructure (``houses.sheets``) has no domain knowledge.
"""

from __future__ import annotations

import logging

from houses.sheets.rules import (
    GREEN_BG,
    GREY_TEXT,
    ORANGE_BG,
    RED_BG,
    add_rule,
    add_time_tiered,
)

logger = logging.getLogger(__name__)

__all__ = [
    "apply_color_rules",
    "apply_data_validations",
]


# ── Generic helpers ─────────────────────────────────────────────────────


def _add_epc_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    """EPC Rating: A/B green, C/D orange, E/F/G red."""
    letter = col_letter_fn(header_lookup["epc rating"])
    add_rule(
        fmt_requests,
        sid,
        header_lookup,
        col_letter_fn,
        "epc rating",
        f'=OR(LEFT(${letter}2,1)="A",LEFT(${letter}2,1)="B")',
        GREEN_BG,
    )
    add_rule(
        fmt_requests,
        sid,
        header_lookup,
        col_letter_fn,
        "epc rating",
        f'=OR(LEFT(${letter}2,1)="C",LEFT(${letter}2,1)="D")',
        ORANGE_BG,
    )
    f = f'=OR(LEFT(${letter}2,1)="E",LEFT(${letter}2,1)="F",LEFT(${letter}2,1)="G")'
    add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "epc rating", f, RED_BG)


def _add_commute_time_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    """Simon/Lorena: <45m green, 45-75m orange, >75m red. Bracknell: <30/30-60/>60."""
    add_time_tiered(fmt_requests, sid, header_lookup, col_letter_fn, "simon london", 0, 45, 1, 15)
    add_time_tiered(fmt_requests, sid, header_lookup, col_letter_fn, "lorena london", 0, 45, 1, 15)
    add_time_tiered(fmt_requests, sid, header_lookup, col_letter_fn, "bracknell time", 0, 30, 1, 0)


def _add_walk_time_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    """Walk to Town, Primary Walk, Secondary Walk, Secondary Bus: <15/15-30/>30."""
    for hdr in ["walk to town", "primary walk", "secondary walk", "secondary bus"]:
        add_time_tiered(fmt_requests, sid, header_lookup, col_letter_fn, hdr, 0, 15, 0, 30)


def _add_ofsted_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    """Primary/Secondary Ofsted: Outstanding green, Good orange, Requires Improvement/Inadequate red."""
    for hdr in ["primary ofsted", "secondary ofsted"]:
        letter = col_letter_fn(header_lookup[hdr])
        add_rule(fmt_requests, sid, header_lookup, col_letter_fn, hdr, f'=${letter}2="Outstanding"', GREEN_BG)
        add_rule(fmt_requests, sid, header_lookup, col_letter_fn, hdr, f'=LEFT(${letter}2,4)="Good"', ORANGE_BG)
        f = f'=OR(LEFT(${letter}2,20)="Requires Improvement",LEFT(${letter}2,9)="Inadequate")'
        add_rule(fmt_requests, sid, header_lookup, col_letter_fn, hdr, f, RED_BG)


def _add_inspection_year_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    """Inspection years: >=2023 green, <=2022 orange. 2-tier only."""
    for hdr in ["primary inspection year", "secondary inspection year"]:
        letter = col_letter_fn(header_lookup[hdr])
        add_rule(
            fmt_requests,
            sid,
            header_lookup,
            col_letter_fn,
            hdr,
            f'=AND(${letter}2<>"",VALUE(${letter}2)>=2023)',
            GREEN_BG,
        )
        add_rule(
            fmt_requests,
            sid,
            header_lookup,
            col_letter_fn,
            hdr,
            f'=AND(${letter}2<>"",VALUE(${letter}2)>0,VALUE(${letter}2)<=2022)',
            ORANGE_BG,
        )


def _add_grey_text_row_rule(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn, num_cols: int):
    """Full-row grey text when Status column is 'No'. Applied LAST so text dims but backgrounds stay."""
    status_letter = col_letter_fn(header_lookup["status"])
    fmt_requests.append(
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{"sheetId": sid, "startColumnIndex": 0, "endColumnIndex": num_cols, "startRowIndex": 1}],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": f'=${status_letter}2="No"'}],
                        },
                        "format": {"textFormat": {"foregroundColor": GREY_TEXT}},
                    },
                }
            }
        }
    )


def _add_design_color_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    idx = header_lookup.get("design needed")
    if idx is None:
        return
    letter = col_letter_fn(idx)
    add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "design needed", f'=${letter}2="Yes"', ORANGE_BG)
    add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "design needed", f'=${letter}2="No"', GREEN_BG)


def _add_planning_color_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    idx = header_lookup.get("planning needed")
    if idx is None:
        return
    letter = col_letter_fn(idx)
    add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "planning needed", f'=${letter}2="Yes"', ORANGE_BG)
    add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "planning needed", f'=${letter}2="No"', GREEN_BG)
    add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "planning needed", f'=${letter}2="Yikes"', RED_BG)


# ── Status data validation ──────────────────────────────────────────────


def _add_status_data_validation(fmt_requests: list, sid: int, header_lookup: dict):
    """Add dropdown validation (No, Maybe) to the Status column."""
    status_idx = header_lookup.get("status")
    if status_idx is not None:
        fmt_requests.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sid,
                        "startColumnIndex": status_idx,
                        "endColumnIndex": status_idx + 1,
                        "startRowIndex": 1,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [
                                {"userEnteredValue": "No"},
                                {"userEnteredValue": "Maybe"},
                            ],
                        },
                        "showCustomUi": True,
                        "strict": "true",
                    },
                }
            }
        )


def _add_design_data_validation(fmt_requests: list, sid: int, header_lookup: dict):
    idx = header_lookup.get("design needed")
    if idx is not None:
        fmt_requests.append(
            {
                "setDataValidation": {
                    "range": {"sheetId": sid, "startColumnIndex": idx, "endColumnIndex": idx + 1, "startRowIndex": 1},
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": "Yes"}, {"userEnteredValue": "No"}],
                        },
                        "showCustomUi": True,
                        "strict": "true",
                    },
                }
            }
        )


def _add_planning_data_validation(fmt_requests: list, sid: int, header_lookup: dict):
    idx = header_lookup.get("planning needed")
    if idx is not None:
        fmt_requests.append(
            {
                "setDataValidation": {
                    "range": {"sheetId": sid, "startColumnIndex": idx, "endColumnIndex": idx + 1, "startRowIndex": 1},
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [
                                {"userEnteredValue": "Yes"},
                                {"userEnteredValue": "No"},
                                {"userEnteredValue": "Yikes"},
                            ],
                        },
                        "showCustomUi": True,
                        "strict": "true",
                    },
                }
            }
        )


# ── Public API ──────────────────────────────────────────────────────────


def apply_color_rules(fmt_requests: list, sid: int, headers: list[str], col_letter_fn) -> None:
    """Add all domain-specific conditional formatting rules.

    Called by ``View.sync()`` to populate the View tab's conditional formats.
    """
    header_lookup = {h.strip().lower(): i for i, h in enumerate(headers)}

    _add_epc_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_commute_time_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_walk_time_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_ofsted_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_inspection_year_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_design_color_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_planning_color_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_grey_text_row_rule(fmt_requests, sid, header_lookup, col_letter_fn, len(headers))


def apply_data_validations(fmt_requests: list, sid: int, headers: list[str]) -> None:
    """Add all domain-specific data validation rules.

    Called by ``View.sync()`` to populate the View tab's dropdown validations.
    """
    header_lookup = {h.strip().lower(): i for i, h in enumerate(headers)}
    _add_status_data_validation(fmt_requests, sid, header_lookup)
    _add_design_data_validation(fmt_requests, sid, header_lookup)
    _add_planning_data_validation(fmt_requests, sid, header_lookup)
