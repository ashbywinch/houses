"""Migration: add Simon Parking Cost (£) column to live sheet.

Inserts a column at position 12 (after Simon London Route) in the
Properties Data tab, writes the header, then syncs named ranges and
View tab formulas.

Usage:
    uv run python scripts/migrate_sheet_add_parking_column.py
"""

from __future__ import annotations

import json
import logging
import os
import sys

import gspread
from google.oauth2.service_account import Credentials

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from houses.sheets import (  # noqa: E402
    COLUMN_HEADERS,
    ensure_named_ranges,
    sync_view_formulas,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DATA_TAB = "Properties Data"
NEW_HEADER = "Simon Parking Cost (£)"


def main():
    raw = os.environ.get("GOOGLE_SHEETS_SERVICE_ACCOUNT") or os.environ.get("HOUSES_SERVICE_ACCOUNT")
    sheet_id = os.environ.get("HOUSES_SHEET_ID")

    if not raw or not sheet_id:
        logger.error("GOOGLE_SHEETS_SERVICE_ACCOUNT and HOUSES_SHEET_ID must be set")
        sys.exit(1)

    creds = Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    # Verify the column isn't already present
    ws = sh.worksheet(DATA_TAB)
    headers = ws.row_values(1)
    if NEW_HEADER in headers:
        logger.info("Column '%s' already exists — nothing to do", NEW_HEADER)
        return

    # Find where to insert: after "Simon London Route" (currently at index 10)
    try:
        insert_after = headers.index("Simon London Route")
    except ValueError:
        logger.error("Could not find 'Simon London Route' in headers")
        sys.exit(1)

    insert_pos = insert_after + 2  # 1-indexed, after the column

    logger.info(
        "Inserting column %s at position %d (after '%s')",
        NEW_HEADER,
        insert_pos,
        headers[insert_after],
    )

    # Insert a blank column at the correct position
    ws.insert_cols([[""]], col=insert_pos)

    # Write the header
    ws.update_cell(1, insert_pos, NEW_HEADER)
    logger.info("Header written")

    # Sync named ranges to reflect the new column layout
    ensure_named_ranges(sh)
    logger.info("Named ranges updated")

    # Rewrite View formulas — they use named ranges so they adapt automatically
    sync_view_formulas(sh)
    logger.info("View formulas synced")

    logger.info("Migration complete. New column '%s' added at column %s", NEW_HEADER, chr(64 + insert_pos))


if __name__ == "__main__":
    main()
