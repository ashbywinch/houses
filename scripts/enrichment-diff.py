"""Compare live sheet contents with a fresh (no-write) re-enrichment.

Usage::

    uv run python scripts/enrichment-diff.py > /tmp/diff.tsv

The local server must be running (``make run``).  The script reads the live
sheet, triggers a forced re-enrichment via the backfill endpoint, and
writes a TSV of differences to stdout.

The backfill endpoint outputs ``"flat"`` dicts whose keys are the canonical
column header names — the same names used by ``col_index`` in ``sheets.py``.
This means exact matching (no fuzziness) works correctly.
"""

from __future__ import annotations

import csv
import json
import sys
import urllib.request
from pathlib import Path

from houses.sheets import COLUMN_HEADERS, col_index

BACKFILL_URL = (
    "http://localhost:8080/backfill-view?no_write=true&force=true"
    "&fields=simon&fields=lorena&fields=petrol&fields=schools"
    "&fields=walk_time&fields=amenities&fields=town&fields=epc"
    "&fields=council_tax&fields=geo"
)


def main():
    print("=== enrichment-diff ===", file=sys.stderr)

    # ── 1. Read live sheet ──────────────────────────────────────────
    import gspread
    from google.oauth2.service_account import Credentials
    from houses.config import settings

    print("Reading live sheet...", file=sys.stderr)
    creds = Credentials.from_service_account_info(
        json.loads(settings.service_account_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(settings.sheet_id)
    ws = sh.worksheet("Properties Data")
    all_rows = ws.get_all_values()
    live_headers = all_rows[0]
    rid_col = col_index("Rightmove ID")
    print(f"  {len(all_rows) - 1} properties, {len(live_headers)} columns", file=sys.stderr)

    # ── 2. Trigger backfill ─────────────────────────────────────────
    print("Triggering no_write backfill...", file=sys.stderr)
    try:
        req = urllib.request.Request(BACKFILL_URL, method="POST")
        resp = urllib.request.urlopen(req, timeout=600)
    except urllib.error.URLError:
        print(
            "ERROR: Could not connect to local server at localhost:8080.\n"
            f"  Start the server with:  make run\n",
            file=sys.stderr,
        )
        sys.exit(1)

    enriched_rows: dict[str, dict[str, str]] = {}
    for line in resp:
        line = line.decode()
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "row" and d.get("status") == "cached":
            rid = d.get("rid", "")
            flat = d.get("flat")
            if flat:
                enriched_rows[rid] = flat

    print(f"  Enriched {len(enriched_rows)} properties", file=sys.stderr)
    if not enriched_rows:
        print("  ERROR: no enriched data returned.", file=sys.stderr)
        sys.exit(1)

    # ── 3. Compare every column ─────────────────────────────────────
    writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
    writer.writerow(["RID", "Field", "Old (sheet)", "New (enriched)"])

    diff_count = 0
    for row in all_rows[1:]:
        rid = row[rid_col].strip() if rid_col < len(row) else ""
        if not rid or rid not in enriched_rows:
            continue
        new = enriched_rows[rid]

        for col_idx_raw, header in enumerate(all_rows[0]):
            header_stripped = header.strip()
            if not header_stripped or header_stripped in ("Rightmove ID",):
                continue
            old_val = row[col_idx_raw].strip() if col_idx_raw < len(row) else ""
            new_val = new.get(header_stripped, "")

            if old_val != new_val:
                diff_count += 1
                writer.writerow([rid, header_stripped, old_val, new_val])

    writer.writerow([])
    writer.writerow(["DIFF_COUNT", str(diff_count), "", ""])
    print(f"\n  {diff_count} differences across {len(enriched_rows)} properties.", file=sys.stderr)


if __name__ == "__main__":
    main()
