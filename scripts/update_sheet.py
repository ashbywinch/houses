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
from collections.abc import Iterable

import gspread
from fastapi.testclient import TestClient
from google.oauth2.service_account import Credentials

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import contextlib

from houses.settings import settings
from houses.property import EnrichedProperty
from houses.server import app
from houses.sheets import COLUMN_HEADERS, col_index, col_letter, row_values

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID = os.environ.get("HOUSES_SHEET_ID", settings.sheet_id)
DATA_TAB = "Properties Data"

# Columns that the user fills in manually — never overwrite
# lucidlint: ignore global-state static config set (manually filled columns); never mutated — only membership-tested
MANUAL_COLS = {
    col_index("Rightmove URL"),
    col_index("Address"),
    col_index("Postcode"),
    col_index("Bedrooms"),
    col_index("Price (£)"),
    col_index("Actual Latitude"),
    col_index("Actual Longitude"),
}
HTTP_OK = 200
DRY_RUN_DISPLAY_LIMIT = 20


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
# lucidlint: ignore global-state static column → enrichment-field mapping table; never mutated
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


def _fields_for_columns(col_indices: Iterable[int]) -> str:
    """Derive the ?fields= query string for a set of column indices."""
    return ",".join(sorted({_COLUMN_FIELDS[idx] for idx in col_indices if idx in _COLUMN_FIELDS}))


# lucidlint: ignore record-shape CLI-parse result triple — a NamedTuple is ceremony for a local argv parse
def _parse_cli_args() -> tuple[set[int] | None, bool, bool]:
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
    return columns, dry_run, obliterate


def _connect_sheet():
    creds = Credentials.from_service_account_info(json.loads(settings.service_account_json), scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    return sh.worksheet(DATA_TAB)


# lucidlint: ignore record-shape sheet rows matrix — spreadsheet boundary owns the shape
def _ensure_safe_regeneration(headers: list[str], existing: list[list[str]], columns, obliterate: bool) -> None:
    """Refuse to regenerate all enriched columns without explicit consent."""
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


# lucidlint: ignore record-shape HTTP payload dict — serialization boundary owns the shape
def _build_row_payload(row: list[str]) -> dict | None:
    """Build the /inject-property payload; None when no URL can be derived."""
    # Read URL from column A (user-provided). If absent, construct from Rightmove ID.
    url = row[0].strip() if row else ""
    if not url.startswith("http"):
        rid = row[col_index("Rightmove ID")] if len(row) > col_index("Rightmove ID") else ""
        if rid:
            url = f"https://www.rightmove.co.uk/properties/{rid}"
        else:
            return None

    payload = {"url": url}
    addr_col = col_index("Address")
    if len(row) > addr_col and row[addr_col]:
        payload["address"] = row[addr_col]
    pc_col = col_index("Postcode")
    if len(row) > pc_col and row[pc_col]:
        payload["postcode"] = row[pc_col]
    _add_manual_coordinates(payload, row)
    return payload


# lucidlint: ignore record-shape HTTP payload dict — serialization boundary owns the shape
def _add_manual_coordinates(payload: dict, row: list[str]) -> None:
    """Pass user-filled actual values if they exist."""
    lat_col = col_index("Actual Latitude")
    lng_col = col_index("Actual Longitude")
    rid_col = col_index("Rightmove ID")
    if len(row) > lat_col and row[lat_col]:
        with contextlib.suppress(ValueError):
            payload["actual_latitude"] = float(row[lat_col])
    if len(row) > lng_col and row[lng_col]:
        with contextlib.suppress(ValueError):
            payload["actual_longitude"] = float(row[lng_col])
    if len(row) > rid_col and row[rid_col]:
        payload["actual_postcode"] = row[rid_col]


def _choose_needed_columns(headers: list[str], row: list[str], columns) -> list[int] | None:
    """Return the enriched columns to request for this row, or None to skip it."""
    enriched_cols = [i for i in range(len(headers)) if i not in MANUAL_COLS]
    empty_columns = [i for i in enriched_cols if i < len(row) and not row[i].strip()]
    if columns is not None:
        # User specified columns — only those, even if already filled
        return [i for i in columns if i in enriched_cols]
    if not empty_columns:
        # All enriched columns are already populated — nothing to do
        return None
    return empty_columns


# lucidlint: ignore record-shape (cells, changes) build pair — a NamedTuple is ceremony for a local loop
def _build_cell_updates(headers, row, new_row, update_cols, row_idx) -> tuple[list, list]:
    """Return (cells to write, dry-run change rows) for this row."""
    cells = []
    changes = []
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
            changes.append(
                (row_idx, headers[col_idx] if col_idx < len(headers) else f"?{col_idx}", old_val[:40], new_val[:40])
            )
    return cells, changes


# lucidlint: ignore record-shape (rows, cells) accounting pair — a NamedTuple is ceremony for local counters
def _apply_updates(ws, cells, dry_run: bool) -> tuple[int, int]:
    """Write cells to the sheet (unless dry-run); return (rows, cells) changed."""
    if not cells:
        return 0, 0
    if not dry_run:
        ws.spreadsheet.values_batch_update({"valueInputOption": "USER_ENTERED", "data": cells})
    return 1, len(cells)


def _print_summary(dry_run: bool, changed_rows: int, changed_cells: int, dry_run_changes) -> None:
    if dry_run:
        print(f"DRY RUN — {changed_rows} rows would change ({changed_cells} cells)")
        if dry_run_changes:
            print("\nChanges:")
            for row_idx, col_header, old_val, new_val in dry_run_changes[:20]:
                print(f"  Row {row_idx}, {col_header}: '{old_val}' → '{new_val}'")
            if len(dry_run_changes) > DRY_RUN_DISPLAY_LIMIT:
                print(f"  ... and {len(dry_run_changes) - DRY_RUN_DISPLAY_LIMIT} more cells")
    else:
        print(f"Updated {changed_rows} rows ({changed_cells} cells changed)")


def main():
    columns, dry_run, obliterate = _parse_cli_args()

    ws = _connect_sheet()
    existing = ws.get_all_values()

    if not existing or len(existing) < 2:
        print("Data tab is empty — nothing to update")
        return

    headers = existing[0]
    _ensure_safe_regeneration(headers, existing, columns, obliterate)

    client = TestClient(app)
    changed_rows = 0
    changed_cells = 0
    dry_run_changes: list[tuple[int, str, str, str]] = []  # (row, col_header, old, new)

    for row_idx, row in enumerate(existing[1:], 2):
        payload = _build_row_payload(row)
        if payload is None:
            continue

        needed_cols = _choose_needed_columns(headers, row, columns)
        if needed_cols is None:
            continue

        needed_fields = _fields_for_columns(needed_cols)
        url_params = f"dry_run=true&fields={needed_fields}"
        resp = client.post(f"/inject-property?{url_params}", json=payload, timeout=30)
        if resp.status_code != HTTP_OK:
            continue

        enriched = resp.json().get("data", {})
        if not enriched:
            continue

        # Build new row from server response
        new_row = row_values(EnrichedProperty(**enriched))
        cells, changes = _build_cell_updates(headers, row, new_row, needed_cols, row_idx)
        dry_run_changes.extend(changes)
        row_delta, cell_delta = _apply_updates(ws, cells, dry_run)
        changed_rows += row_delta
        changed_cells += cell_delta

    _print_summary(dry_run, changed_rows, changed_cells, dry_run_changes)


if __name__ == "__main__":
    main()
