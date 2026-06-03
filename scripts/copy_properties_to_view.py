#!/usr/bin/env python3
"""One-time script: copy comments and statuses from Properties tab to Properties View tab.

Reads data from the Properties tab, matches rows in the View tab by Rightmove ID,
copies Group Notes / WhatsApp and Ashby comments, and parses status into
Status (No/Maybe) + Status Reason columns. Source tab is preserved.

Usage: uv run python scripts/copy_properties_to_view.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys

import gspread
from google.oauth2.service_account import Credentials

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from houses.sheets import col_letter  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

PROPS_TAB = "Properties"
VIEW_TAB = "Properties View"


def _get_service_account_json() -> str:
    raw = (
        os.environ.get("GOOGLE_SHEETS_SERVICE_ACCOUNT")
        or os.environ.get("HOUSES_GOOGLE_SHEETS_SERVICE_ACCOUNT")
        or ""
    )
    if raw:
        return raw
    from houses.config import settings

    return settings.service_account_json


def _get_sheet_id() -> str:
    sid = os.environ.get("HOUSES_SHEET_ID")
    if sid:
        return sid
    from houses.config import settings

    return settings.sheet_id


def _build_header_index(headers: list[str]) -> dict[str, int]:
    return {h.strip().lower(): i for i, h in enumerate(headers)}


def _extract_rid(link: str) -> str | None:
    m = re.search(r"properties/(\d+)", link)
    return m.group(1) if m else None


def _parse_status(raw: str) -> tuple[str, str]:
    """Return (status, reason) from Properties tab status text.

    Known patterns:
      "Swerve (cost grounds)" -> ("No", "cost grounds")
      "Swerve (suspected money pit)" -> ("No", "suspected money pit")
      "Doesn't work - steps" -> ("No", "steps")
      "No parking = compromise too many..." -> ("No", "No parking = compromise too many...")
      "" -> ("", "")
    """
    if not raw or not raw.strip():
        return ("", "")
    text = raw.strip()
    m = re.match(r"^Swerve\s*\((.+)\)$", text, re.IGNORECASE)
    if m:
        return ("No", m.group(1).strip())
    m = re.match(r"^Doesn'?t\s+work\s*[-:]\s*(.+)$", text, re.IGNORECASE)
    if m:
        return ("No", m.group(1).strip())
    return ("No", text)


def main() -> None:
    raw_json = _get_service_account_json()
    if not raw_json:
        logger.error(
            "No service account JSON found. "
            "Set GOOGLE_SHEETS_SERVICE_ACCOUNT or HOUSES_GOOGLE_SHEETS_SERVICE_ACCOUNT."
        )
        sys.exit(1)

    creds = Credentials.from_service_account_info(
        json.loads(raw_json), scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sheet_id = _get_sheet_id()
    sh = gc.open_by_key(sheet_id)

    props_ws = sh.worksheet(PROPS_TAB)
    view_ws = sh.worksheet(VIEW_TAB)

    view_headers = view_ws.get_all_values()[0]
    view_cols = _build_header_index(view_headers)

    if "status reason" not in view_cols and "status" in view_cols:
        status_idx = view_cols["status"]
        sid_val = view_ws._properties["sheetId"]
        sh.batch_update({
            "requests": [
                {
                    "insertDimension": {
                        "range": {
                            "sheetId": sid_val,
                            "dimension": "COLUMNS",
                            "startIndex": status_idx + 1,
                            "endIndex": status_idx + 2,
                        }
                    }
                }
            ]
        })
        view_ws.update_cell(1, status_idx + 2, "Status Reason")
        view_headers = view_ws.get_all_values()[0]
        view_cols = _build_header_index(view_headers)
        logger.info("Inserted 'Status Reason' column after 'Status'")
    elif "status reason" not in view_cols:
        logger.warning("'Status' column not found in View tab; cannot insert 'Status Reason'")

    props_data = props_ws.get_all_values()
    props_headers = props_data[0]
    props_cols = _build_header_index(props_headers)

    if "rightmove link" not in props_cols:
        logger.error("'Rightmove Link' column not found in Properties tab")
        sys.exit(1)

    view_data = view_ws.get_all_values()

    copied = 0
    skipped = 0

    for _, row in enumerate(props_data[1:], 2):
        if not any(cell.strip() for cell in row):
            continue

        link_col = props_cols["rightmove link"]
        link = row[link_col] if link_col < len(row) else ""
        rid = _extract_rid(link)
        if not rid:
            skipped += 1
            continue

        view_row_num = None
        for i, vrow in enumerate(view_data[1:], 2):
            rid_col = view_cols.get("rightmove id")
            if rid_col is not None and len(vrow) > rid_col and vrow[rid_col].strip() == rid:
                view_row_num = i
                break

        if view_row_num is None:
            logger.warning("Rightmove ID %s not found in View tab", rid)
            skipped += 1
            continue

        updates = []

        src_col_gn = props_cols.get("group notes / whatsapp comments")
        if src_col_gn is not None and src_col_gn < len(row) and row[src_col_gn].strip():
            dst_col = view_cols.get("group notes / whatsapp")
            if dst_col is not None:
                cl = col_letter(dst_col)
                updates.append(
                    {"range": f"{cl}{view_row_num}", "values": [[row[src_col_gn]]]}
                )

        src_col_ac = props_cols.get("ashby comments")
        if src_col_ac is not None and src_col_ac < len(row) and row[src_col_ac].strip():
            dst_col = view_cols.get("ashby comments")
            if dst_col is not None:
                cl = col_letter(dst_col)
                updates.append(
                    {"range": f"{cl}{view_row_num}", "values": [[row[src_col_ac]]]}
                )

        src_col_st = props_cols.get("status")
        if src_col_st is not None and src_col_st < len(row):
            raw_status = row[src_col_st].strip() if row[src_col_st] else ""
            status_val, reason_val = _parse_status(raw_status)

            dst_status_col = view_cols.get("status")
            if dst_status_col is not None and status_val:
                cl = col_letter(dst_status_col)
                updates.append(
                    {"range": f"{cl}{view_row_num}", "values": [[status_val]]}
                )

            dst_reason_col = view_cols.get("status reason")
            if dst_reason_col is not None and reason_val:
                cl = col_letter(dst_reason_col)
                updates.append(
                    {"range": f"{cl}{view_row_num}", "values": [[reason_val]]}
                )

        if updates:
            view_ws.spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": updates}
            )
            copied += 1

    logger.info(
        "Copied data for %d properties, skipped %d (not found in View tab)",
        copied,
        skipped,
    )


if __name__ == "__main__":
    main()
