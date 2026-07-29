"""One-shot migration: read Ashby Works Estimate from the Data tab
and push into the DAG as works_estimates dicts.

Run this ONCE per deployment to import existing sheet values::

    uv run python tools/migrate_works_from_sheet.py

After this, edits flow through the PATCH endpoint and sheet sync.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from houses.config import settings
from houses.sheets import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

VIEW_TAB = "Properties View"

def read_view_tab_works() -> dict[str, str]:
    """Read Ashby Works Estimate (£) column from the View tab as fallback."""
    client = get_client()
    if not client:
        return {}

    sh = client.open_by_key(settings.sheet_id)

    try:
        ws = sh.worksheet(VIEW_TAB)
    except Exception:
        logger.warning("View tab not found, skipping")
        return {}

    all_rows = ws.get_all_values()
    headers = all_rows[0]

    try:
        rid_idx = headers.index("Rightmove ID")
        works_idx = headers.index("Ashby Works Estimate (£)")
    except ValueError:
        logger.warning("Ashby Works Estimate column not found in View tab")
        return {}

    result: dict[str, str] = {}
    for row in all_rows[1:]:
        if not row or not row[0].strip():
            continue
        rid = (row[rid_idx] or "").strip()
        val = (row[works_idx] or "").strip()
        if rid and val:
            result[rid] = val

    logger.info("Read %d works estimates from View tab", len(result))
    return result


def push_to_dag(rid: str, value: float) -> bool:
    """Push a works estimate dict to the property's DAG node."""
    from dag.persistence import save_node_result
    from datetime import datetime, UTC

    node_id = f"{rid}/works_estimates"
    result_dict = {
        "status": "succeeded",
        "value": {"Ashby": value},
        "error": "",
        "provenance": {"label": "sheet-migration"},
    }
    dep_timestamps: dict[str, str] = {}
    now = datetime.now(UTC).isoformat()

    try:
        save_node_result(node_id, result_dict, dep_timestamps, created_at=now)
        return True
    except Exception as e:
        logger.warning("Failed to push for %s: %s", rid, e)
        return False


def parse_value(raw: str) -> float | None:
    """Parse a works estimate value from a sheet cell. Handles £, commas, empty."""
    cleaned = raw.replace("£", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        logger.warning("Could not parse value %r", raw)
        return None


def main() -> None:
    logger.info("Starting works estimate migration from sheet")

    works = read_view_tab_works()

    if not works:
        logger.info("No works estimates found in sheet — nothing to migrate")
        return

    pushed = 0
    skipped = 0
    for rid, raw_val in works.items():
        val = parse_value(raw_val)
        if val is None:
            skipped += 1
            continue
        if push_to_dag(rid, val):
            pushed += 1
        else:
            skipped += 1

    logger.info("Migration complete: %d pushed, %d skipped", pushed, skipped)


if __name__ == "__main__":
    main()
