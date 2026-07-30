"""One-shot migration: read Ashby comments and Group Notes from the
View tab and push into the comments table.

Run once per deployment::

    uv run python tools/migrate_comments_from_sheet.py

Ashby comments are attributed to Ashby, Group Notes to Simon.
"""

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from houses.config import settings
from houses.database import get_connection
from houses.sheets import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

VIEW_TAB = "Properties View"


def read_view_comments() -> dict[str, dict[str, str]]:
    """Read Ashby comments and Group Notes from the View tab.

    Returns {rid: {"ashby": str, "group": str}}.
    """
    client = get_client()
    if not client:
        logger.error("No sheet client")
        sys.exit(1)

    sh = client.open_by_key(settings.sheet_id)
    ws = sh.worksheet(VIEW_TAB)
    all_rows = ws.get_all_values()
    headers = all_rows[0]

    try:
        rid_idx = headers.index("Rightmove ID")
        ashby_idx = headers.index("Ashby comments")
        group_idx = headers.index("Group Notes / WhatsApp")
    except ValueError as e:
        logger.error("Required column not found: %s", e)
        sys.exit(1)

    result: dict[str, dict[str, str]] = {}
    for row in all_rows[1:]:
        if not row or not row[0].strip():
            continue
        rid = (row[rid_idx] or "").strip()
        if not rid:
            continue
        ashby_val = (row[ashby_idx] or "").strip()
        group_val = (row[group_idx] or "").strip()
        if ashby_val or group_val:
            result[rid] = {"ashby": ashby_val, "group": group_val}

    logger.info("Read %d properties with comments from View tab", len(result))
    return result


def main() -> None:
    logger.info("Starting comments migration from sheet")

    comments = read_view_comments()
    if not comments:
        logger.info("No comments found in sheet")
        return

    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    inserted = 0
    skipped = 0

    for rid, vals in comments.items():
        if vals["ashby"]:
            # Check if already migrated
            existing = conn.execute(
                "SELECT COUNT(*) FROM comments WHERE rid = ? AND person = 'Ashby' AND text = ?",
                (rid, vals["ashby"]),
            ).fetchone()[0]
            if existing == 0:
                conn.execute(
                    "INSERT INTO comments (rid, person, text, created_at) VALUES (?, ?, ?, ?)",
                    (rid, "Ashby", vals["ashby"], now),
                )
                inserted += 1
            else:
                skipped += 1

        if vals["group"]:
            existing = conn.execute(
                "SELECT COUNT(*) FROM comments WHERE rid = ? AND person = 'Simon' AND text = ?",
                (rid, vals["group"]),
            ).fetchone()[0]
            if existing == 0:
                conn.execute(
                    "INSERT INTO comments (rid, person, text, created_at) VALUES (?, ?, ?, ?)",
                    (rid, "Simon", vals["group"], now),
                )
                inserted += 1
            else:
                skipped += 1

    conn.commit()
    logger.info("Done: %d inserted, %d already existed", inserted, skipped)


if __name__ == "__main__":
    main()
