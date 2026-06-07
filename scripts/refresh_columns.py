"""Re-run enrichment for specified fields, overwriting existing values.

Usage:
    uv run python scripts/refresh_columns.py simon,lorena
    uv run python scripts/refresh_columns.py petrol,schools

Reads from the Properties Data tab, passes each row through
_run_backfill_enrichment, and writes all column headers belonging to
the requested field groups — overwriting existing cell values.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from houses.server import _run_backfill_enrichment, _ENRICHMENT_FIELD_COLUMNS
from houses.sheets import Tab, _row_values, col_letter, get_client
from houses.config import settings


async def main():
    fields = sys.argv[1].split(",") if len(sys.argv) > 1 else ["simon", "lorena"]
    field_set = set(fields)

    # All column headers that belong to the requested fields
    headers_to_write: set[str] = set()
    for f in fields:
        headers_to_write.update(_ENRICHMENT_FIELD_COLUMNS.get(f, set()))

    gclient = get_client()
    sh = gclient.open_by_key(settings.sheet_id)
    ws = sh.worksheet("Properties Data")
    rows = ws.get_all_values()
    sheet_headers = rows[0]
    h = {name: i for i, name in enumerate(sheet_headers)}

    total = len(rows) - 1
    for row_idx, row in enumerate(rows[1:], 2):
        url = row[h["Rightmove URL"]].strip() if h["Rightmove URL"] < len(row) else ""
        addr = row[h["Address"]].strip() if h["Address"] < len(row) else ""
        pc = row[h["Postcode"]].strip() if h["Postcode"] < len(row) else ""

        if not pc and not url:
            print(f"[{row_idx}/{total}] Skipping — no url or postcode")
            continue

        enriched = await _run_backfill_enrichment(
            url=url, address=addr, postcode=pc, lookup="",
            bedrooms=None, price=None, enabled=field_set,
        )

        values = _row_values(enriched)

        cells: list[dict] = []
        for header in headers_to_write:
            if header in h:
                val = values.get(header, "")
                cells.append({
                    "range": f"{col_letter(h[header])}{row_idx}",
                    "values": [[val]],
                })

        if cells:
            Tab(ws).batch_update(cells)

        # Print summary — pick the "minutes" column for each field group
        parts: list[str] = []
        for f in fields:
            cols = _ENRICHMENT_FIELD_COLUMNS.get(f, set())
            min_col = next((c for c in cols if "(min)" in c or "Time" in c), None)
            if min_col:
                parts.append(f"{f}={values.get(min_col, '?')}")
        summary = ", ".join(parts) if parts else f"fields={','.join(fields)}"
        print(f"[{row_idx}/{total}] {pc or addr}: {summary}")

    print(f"\nDone — {total} rows processed, fields={','.join(fields)}")


if __name__ == "__main__":
    asyncio.run(main())
