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
    RuleContext,
    TimeThresholds,
)

logger = logging.getLogger(__name__)

__all__ = [
    "apply_color_rules",
    "apply_data_validations",
]


# ── Generic helpers ─────────────────────────────────────────────────────


def _add_epc_rules(ctx: RuleContext):
    """EPC Rating: A/B green, C/D orange, E/F/G red."""
    letter = ctx.col_letter_fn(ctx.header_lookup["epc rating"])
    ctx.add("epc rating",
        f'=OR(LEFT(${letter}2,1)="A",LEFT(${letter}2,1)="B")',
        GREEN_BG,
    )
    ctx.add("epc rating",
        f'=OR(LEFT(${letter}2,1)="C",LEFT(${letter}2,1)="D")',
        ORANGE_BG,
    )
    f = f'=OR(LEFT(${letter}2,1)="E",LEFT(${letter}2,1)="F",LEFT(${letter}2,1)="G")'
    ctx.add("epc rating", f, RED_BG)


def _add_commute_time_rules(ctx: RuleContext):
    """Simon/Lorena: <45m green, 45-75m orange, >75m red. Bracknell: <30/30-60/>60."""
    ctx.time_tiered(
        header="simon london",
        thresholds=TimeThresholds(green_hours=0, green_mins=45, orange_hours=1, orange_mins=15),
    )
    ctx.time_tiered(
        header="lorena london",
        thresholds=TimeThresholds(green_hours=0, green_mins=45, orange_hours=1, orange_mins=15),
    )
    ctx.time_tiered(
        header="bracknell time",
        thresholds=TimeThresholds(green_hours=0, green_mins=30, orange_hours=1, orange_mins=0),
    )


def _add_walk_time_rules(ctx: RuleContext):
    """Walk to Town, Primary Walk, Secondary Walk, Secondary Bus: <15/15-30/>30."""
    for hdr in ["walk to town", "primary walk", "secondary walk", "secondary bus"]:
        ctx.time_tiered(
            hdr,
            TimeThresholds(green_hours=0, green_mins=15, orange_hours=0, orange_mins=30),
        )


def _add_ofsted_rules(ctx: RuleContext):
    """Primary/Secondary Ofsted: Outstanding green, Good orange, Requires Improvement/Inadequate red."""
    for hdr in ["primary ofsted", "secondary ofsted"]:
        letter = ctx.col_letter_fn(ctx.header_lookup[hdr])
        ctx.add(hdr, f'=${letter}2="Outstanding"', GREEN_BG)
        ctx.add(hdr, f'=LEFT(${letter}2,4)="Good"', ORANGE_BG)
        f = f'=OR(LEFT(${letter}2,20)="Requires Improvement",LEFT(${letter}2,9)="Inadequate")'
        ctx.add(hdr, f, RED_BG)


def _add_inspection_year_rules(ctx: RuleContext):
    """Inspection years: >=2023 green, <=2022 orange. 2-tier only."""
    for hdr in ["primary inspection year", "secondary inspection year"]:
        letter = ctx.col_letter_fn(ctx.header_lookup[hdr])
        ctx.add(hdr,
            f'=AND(${letter}2<>"",VALUE(${letter}2)>=2023)',
            GREEN_BG,
        )
        ctx.add(hdr,
            f'=AND(${letter}2<>"",VALUE(${letter}2)>0,VALUE(${letter}2)<=2022)',
            ORANGE_BG,
        )


def _add_grey_text_row_rule(ctx: RuleContext, num_cols: int):
    """Full-row grey text when Status column is 'No'. Applied LAST so text dims but backgrounds stay."""
    status_letter = ctx.col_letter_fn(ctx.header_lookup["status"])
    ctx.fmt_requests.append(
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [
                        {"sheetId": ctx.sid, "startColumnIndex": 0, "endColumnIndex": num_cols, "startRowIndex": 1}
                    ],
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


def _add_design_color_rules(ctx: RuleContext):
    idx = ctx.header_lookup.get("design needed")
    if idx is None:
        return
    letter = ctx.col_letter_fn(idx)
    ctx.add("design needed", f'=${letter}2="Yes"', ORANGE_BG)
    ctx.add("design needed", f'=${letter}2="No"', GREEN_BG)


def _add_planning_color_rules(ctx: RuleContext):
    idx = ctx.header_lookup.get("planning needed")
    if idx is None:
        return
    letter = ctx.col_letter_fn(idx)
    ctx.add("planning needed", f'=${letter}2="Yes"', ORANGE_BG)
    ctx.add("planning needed", f'=${letter}2="No"', GREEN_BG)
    ctx.add("planning needed", f'=${letter}2="Yikes"', RED_BG)


# ── Status data validation ──────────────────────────────────────────────


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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
    ctx = RuleContext(
        fmt_requests=fmt_requests,
        sid=sid,
        header_lookup=header_lookup,
        col_letter_fn=col_letter_fn,
    )

    _add_epc_rules(ctx)
    _add_commute_time_rules(ctx)
    _add_walk_time_rules(ctx)
    _add_ofsted_rules(ctx)
    _add_inspection_year_rules(ctx)
    _add_design_color_rules(ctx)
    _add_planning_color_rules(ctx)
    _add_grey_text_row_rule(ctx, len(headers))


def apply_data_validations(fmt_requests: list, sid: int, headers: list[str]) -> None:
    """Add all domain-specific data validation rules.

    Called by ``View.sync()`` to populate the View tab's dropdown validations.
    """
    header_lookup = {h.strip().lower(): i for i, h in enumerate(headers)}
    _add_status_data_validation(fmt_requests, sid, header_lookup)
    _add_design_data_validation(fmt_requests, sid, header_lookup)
    _add_planning_data_validation(fmt_requests, sid, header_lookup)
