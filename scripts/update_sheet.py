"""Selectively update enriched columns in the sheet without trashing manual data.

Usage:
    # Update all enriched columns (re-enrich everything)
    uv run python scripts/update_sheet.py

    # Dry-run: show what would change without writing
    uv run python scripts/update_sheet.py --dry-run

    # Update only specific columns by header name
    uv run python scripts/update_sheet.py --columns "Walk to Town (min),Walkable Amenities"

    # Dry-run specific columns
    uv run python scripts/update_sheet.py --dry-run --columns "Area Description,Simon London (min)"

This reads existing rows from Properties Data, POSTs each property to the
server for fresh enrichment, and writes back only the requested columns.
Manual columns (Rightmove URL, Bedrooms, Actual Lat/Lng/Postcode) are preserved.
"""

import json
import os
import sys

import gspread
from fastapi.testclient import TestClient
from google.oauth2.service_account import Credentials

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import contextlib

from houses.config import settings  # noqa: E402
from houses.server import app  # noqa: E402
from houses.sheets import COLUMN_HEADERS, col_index, col_letter  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID = os.environ.get("HOUSES_SHEET_ID", settings.sheet_id)
DATA_TAB = "Properties Data"

# Columns that the user fills in manually — never overwrite
MANUAL_COLS = {
    col_index("Rightmove URL"),
    col_index("Address"),
    col_index("Postcode"),
    col_index("Bedrooms"),
    col_index("Price (£)"),
    col_index("Actual Latitude"),
    col_index("Actual Longitude"),
}


def _find_closest_header(name: str) -> str | None:
    """Return the closest matching header name, or None if no close match."""
    name_lower = name.lower().strip()
    for h in COLUMN_HEADERS:
        if h.lower() == name_lower:
            return h
    matches = [h for h in COLUMN_HEADERS if name_lower in h.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None
    return None


def parse_columns(arg: str) -> set[int]:
    parts = [p.strip() for p in arg.split(",")]
    indices = set()
    for p in parts:
        if p.isdigit():
            indices.add(int(p))
            continue
        header = _find_closest_header(p)
        if header:
            indices.add(col_index(header))
        else:
            candidates = [h for h in COLUMN_HEADERS if p.lower() in h.lower()]
            if candidates:
                print(f"Column '{p}' is ambiguous. Did you mean: {', '.join(candidates[:5])}?")
            else:
                print(f"Column '{p}' not found. Available: {', '.join(COLUMN_HEADERS)}")
            sys.exit(1)
    return indices


# Column header to enrichment field name mapping.
# When --columns is specified, only the corresponding enrichment modules run,
# saving API credits on unnecessary lookups.
_COLUMN_FIELDS: dict[int, str] = {
    col_index("Simon London (min)"): "simon",
    col_index("Simon London Cost (£)"): "simon",
    col_index("Lorena London (min)"): "lorena",
    col_index("Lorena London Cost (£)"): "lorena",
    col_index("Bracknell Time (min)"): "petrol",
    col_index("Bracknell Cost (£)"): "petrol",
    col_index("Primary School"): "schools",
    col_index("Primary Distance (km)"): "schools",
    col_index("Primary Walk (min)"): "schools",
    col_index("Primary School Link"): "schools",
    col_index("Primary Ofsted"): "schools",
    col_index("Primary Inspection Year"): "schools",
    col_index("Secondary School"): "schools",
    col_index("Secondary Distance (km)"): "schools",
    col_index("Secondary Walk (min)"): "schools",
    col_index("Secondary School Link"): "schools",
    col_index("Secondary Ofsted"): "schools",
    col_index("Secondary Inspection Year"): "schools",
    col_index("Secondary Bus (min)"): "schools",
    col_index("Secondary Bus Route"): "schools",
    col_index("Walk to Town (min)"): "walk_time",
    col_index("Walkable Amenities"): "amenities",
    col_index("Area Description"): "town",
    col_index("EPC Rating"): "epc",
    col_index("Approx Latitude (est)"): "geo",
    col_index("Approx Longitude (est)"): "geo",
    col_index("Approx Station CRS"): "geo",
    col_index("Approx Station Name"): "geo",
}


def _fields_for_columns(col_indices: set[int]) -> str:
    """Derive the ?fields= query string for a set of column indices."""
    needed = set()
    for idx in col_indices:
        if idx in _COLUMN_FIELDS:
            needed.add(_COLUMN_FIELDS[idx])
    return ",".join(sorted(needed))


def main():
    columns = None
    dry_run = False
    obliterate = False
    i = 0
    while i < len(sys.argv[1:]):
        a = sys.argv[1:][i]
        if a == "--columns" and i + 1 < len(sys.argv[1:]):
            columns = parse_columns(sys.argv[1:][i + 1])
            i += 1
        elif a == "--dry-run":
            dry_run = True
        elif a == "--obliterate":
            obliterate = True
        i += 1

    creds = Credentials.from_service_account_info(json.loads(settings.service_account_json), scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(DATA_TAB)
    existing = ws.get_all_values()

    if not existing or len(existing) < 2:
        print("Data tab is empty — nothing to update")
        return

    headers = existing[0]

    # Safety check: refuse to regenerate all enriched columns without explicit consent.
    enriched_cols = [i for i in range(len(headers)) if i not in MANUAL_COLS]
    already_populated = [i for i in enriched_cols if any(len(r) > i and r[i].strip() for r in existing[1:])]
    if not columns and not obliterate and already_populated:
        populated_names = [headers[i] for i in already_populated[:5]]
        print(
            f"ERROR: {len(already_populated)} enriched columns already have data "
            f"(e.g. {', '.join(populated_names)}...).\n"
            f"Regenerating them would waste API credits on unnecessary lookups.\n"
            f'  Use --columns "Col1,Col2" to target specific columns, or\n'
            f"  Use --obliterate if you really want to regenerate everything."
        )
        sys.exit(1)
    client = TestClient(app)
    changed_rows = 0
    changed_cells = 0
    dry_run_changes: list[tuple[int, str, str, str]] = []  # (row, col_header, old, new)

    for row_idx, row in enumerate(existing[1:], 2):
        # Read URL from column A (user-provided). If absent, construct from Rightmove ID.
        url = row[0].strip() if row else ""
        if not url.startswith("http"):
            rid = row[col_index("Rightmove ID")] if len(row) > col_index("Rightmove ID") else ""
            if rid:
                url = f"https://www.rightmove.co.uk/properties/{rid}"
            else:
                continue

        payload = {"url": url}
        addr_col = col_index("Address")
        if len(row) > addr_col and row[addr_col]:
            payload["address"] = row[addr_col]
        pc_col = col_index("Postcode")
        if len(row) > pc_col and row[pc_col]:
            payload["postcode"] = row[pc_col]
        # Pass user-filled actual values if they exist
        if len(row) > 5 and row[5]:
            with contextlib.suppress(ValueError):
                payload["actual_latitude"] = float(row[5])
        if len(row) > 6 and row[6]:
            with contextlib.suppress(ValueError):
                payload["actual_longitude"] = float(row[6])
        if len(row) > 7 and row[7]:
            payload["actual_postcode"] = row[7]

        # Determine which enriched columns are empty for this row.
        # Only request those field groups from the server to avoid wasting API calls.
        enriched_cols = [i for i in range(len(headers)) if i not in MANUAL_COLS]
        empty_columns = [i for i in enriched_cols if i < len(row) and not row[i].strip()]

        if columns is not None:
            # User specified columns — only those, even if already filled
            needed_cols = [i for i in columns if i in enriched_cols]
        elif not empty_columns:
            # All enriched columns are already populated — nothing to do
            continue
        else:
            needed_cols = empty_columns

        needed_fields = _fields_for_columns(needed_cols)
        url_params = f"dry_run=true&fields={needed_fields}"
        resp = client.post(f"/inject-property?{url_params}", json=payload, timeout=30)
        if resp.status_code != 200:
            continue

        enriched = resp.json().get("data", {})
        if not enriched:
            continue

        # Build new row from server response
        from houses.property import EnrichedProperty
        from houses.sheets import row_values

        new_row = row_values(EnrichedProperty(**enriched))

        update_cols = needed_cols

        cells = []
        for col_idx in update_cols:
            if col_idx >= len(new_row):
                continue
            old_val = row[col_idx] if col_idx < len(row) else ""
            new_val = new_row.get(headers[col_idx] if col_idx < len(headers) else "", "")
            if old_val != new_val:
                cells.append(
                    {
                        "range": f"{DATA_TAB}!{col_letter(col_idx)}{row_idx}",
                        "values": [[new_val]],
                    }
                )
                dry_run_changes.append(
                    (row_idx, headers[col_idx] if col_idx < len(headers) else f"?{col_idx}", old_val[:40], new_val[:40])
                )

        if cells:
            if dry_run:
                changed_rows += 1
                changed_cells += len(cells)
            else:
                ws.spreadsheet.values_batch_update({"valueInputOption": "USER_ENTERED", "data": cells})
                changed_rows += 1
                changed_cells += len(cells)

    if dry_run:
        print(f"DRY RUN — {changed_rows} rows would change ({changed_cells} cells)")
        if dry_run_changes:
            print("\nChanges:")
            for row_idx, col_header, old_val, new_val in dry_run_changes[:20]:
                print(f"  Row {row_idx}, {col_header}: '{old_val}' → '{new_val}'")
            if len(dry_run_changes) > 20:
                print(f"  ... and {len(dry_run_changes) - 20} more cells")
    else:
        print(f"Updated {changed_rows} rows ({changed_cells} cells changed)")


if __name__ == "__main__":
    main()
