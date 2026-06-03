"""gspread integration — write enriched rows to the AI_Data_Source (Bot) tab."""

from __future__ import annotations

import json
import logging
import re

import gspread
from google.oauth2.service_account import Credentials

from houses.config import settings
from houses.models import CommuteBreakdown, EnrichedProperty, PetrolCost, SchoolInfo, TransitInfo

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_TAB = "Properties Data"
COLUMN_HEADERS: list[str] = [
    "Rightmove URL",           # A  (0) — user-owned, never overwrite
    "Address",                 # B  (1) — user-owned, never overwrite
    "Postcode",                # C  (2) — user-owned, never overwrite
    "Bedrooms",                # D  (3) — user-owned, never overwrite
    "Price (£)",               # E  (4) — user-owned, never overwrite
    "Actual Latitude",         # F  (5) — user-owned, never overwrite
    "Actual Longitude",        # G  (6) — user-owned, never overwrite
    "Rightmove ID",            # H  (7) — server-written stable lookup key
    "Simon London (min)",      # I  (8)
    "Simon London Cost (£)",   # J  (9)
    "Lorena London (min)",     # K  (10)
    "Lorena London Cost (£)",  # L  (11)
    "Bracknell Time (min)",    # M  (12)
    "Bracknell Cost (£)",      # N  (13)
    "Primary School",          # O  (14)
    "Primary Distance (km)",   # P  (15)
    "Primary Walk (min)",      # Q  (16)
    "Primary School Link",     # R  (17)
    "Primary Ofsted",          # S  (18)
    "Primary Inspection Year", # T  (19)
    "Primary Inspection Summary",  # U  (20)
    "Secondary School",        # V  (21)
    "Secondary Distance (km)", # W  (22)
    "Secondary Walk (min)",    # X  (23)
    "Secondary School Link",   # Y  (24)
    "Secondary Ofsted",        # Z  (25)
    "Secondary Inspection Year",  # AA (26)
    "Secondary Inspection Summary",  # AB (27)
    "Area Description",        # AC (28)
    "Walk to Town (min)",      # AD (29)
    "Walkable Amenities",      # AE (30)
    "EPC Rating",              # AF (31)
    "Secondary Bus (min)",     # AG (32)
    "Secondary Bus Route",     # AH (33)
    "Approx Latitude (est)",   # AI (34)
    "Approx Longitude (est)",  # AJ (35)
    "Approx Station CRS",      # AK (36)
    "Approx Station Name",     # AL (37)
]

_USER_COLUMNS = frozenset({
    "Rightmove URL", "Address", "Postcode", "Bedrooms", "Price (£)",
    "Actual Latitude", "Actual Longitude",
})


def col_index(header: str) -> int:
    """Return the 0-based column index for a given header name."""
    for i, h in enumerate(COLUMN_HEADERS):
        if h == header:
            return i
    raise ValueError(f"Column '{header}' not found in COLUMN_HEADERS")


# Index positions of user-owned columns (must never be written by the server)
_USER_COL_INDICES = frozenset(
    col_index(h) for h in _USER_COLUMNS
)


def col_letter(i: int) -> str:
    """Convert 0-based column index to Google Sheets column letter."""
    if i < 26:
        return chr(65 + i)
    return chr(64 + i // 26) + chr(65 + i % 26)


def _col_letter(index: int) -> str:
    if index < 26:
        return chr(65 + index)
    return chr(64 + index // 26) + chr(65 + index % 26)


_RIGHTMOVE_ID_RE = re.compile(r"properties/(\d+)")


def _rightmove_id(url_or_text: str) -> str:
    """Extract the numeric Rightmove property ID from a URL or text."""
    m = _RIGHTMOVE_ID_RE.search(url_or_text)
    if m:
        return m.group(1)
    m = re.search(r"(\d{8,})", url_or_text)
    return m.group(1) if m else ""


_client: gspread.Client | None = None


def _build_client() -> gspread.Client | None:
    raw = settings.service_account_json
    if not raw:
        return None
    try:
        creds_dict = json.loads(raw)
        credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        return gspread.authorize(credentials)
    except Exception:
        logger.exception("Failed to authenticate from HOUSES_SERVICE_ACCOUNT_JSON")
        return None


def get_client() -> gspread.Client | None:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def _fmt_duration(t: TransitInfo | None) -> str:
    return str(t.duration_minutes) if t and t.duration_minutes is not None else ""


def _fmt_cost(val: float | None) -> str:
    return f"{val:.2f}" if val is not None else ""


def _fmt_dist(s: SchoolInfo | None) -> str:
    return f"{s.distance_km:.2f}" if s and s.distance_km is not None else ""


def _fmt_walk(s: SchoolInfo | None) -> str:
    return str(s.walking_time_minutes) if s and s.walking_time_minutes is not None else ""


def _fmt_school_link(s: SchoolInfo | None) -> str:
    if s and s.urn:
        return f"https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/{s.urn}"
    return ""


def _fmt_bus(s: SchoolInfo | None) -> str:
    return str(s.bus_time_minutes) if s and s.bus_time_minutes is not None else ""


def _row_values(property_: EnrichedProperty) -> list[str]:
    """Build a row for the sheet. User columns (A-G) are left empty — never overwrite."""
    return [
        "",                                           # A  (0) user-owned
        "",                                           # B  (1) user-owned
        "",                                           # C  (2) user-owned
        "",                                           # D  (3) user-owned
        "",                                           # E  (4) user-owned
        "",                                           # F  (5) user-owned
        "",                                           # G  (6) user-owned
        _rightmove_id(property_.url),                 # H  (7) stable lookup key
        _fmt_duration(property_.simon_commute),             # I  (8)
        _fmt_cost(property_.simon_commute.daily_cost_gbp if property_.simon_commute else None),  # J  (9)
        _fmt_duration(property_.lorena_commute),            # K (10)
        _fmt_cost(property_.lorena_commute.daily_cost_gbp if property_.lorena_commute else None),  # L (11)
        str(property_.petrol.round_trip_minutes) if property_.petrol and property_.petrol.round_trip_minutes is not None else "",  # M (12)
        _fmt_cost(property_.petrol.cost_gbp if property_.petrol else None),  # N (13)
        property_.primary_school.name if property_.primary_school else "",  # O (14)
        _fmt_dist(property_.primary_school),                # P (15)
        _fmt_walk(property_.primary_school),                # Q (16)
        _fmt_school_link(property_.primary_school),         # R (17)
        property_.primary_school.ofsted_rating if property_.primary_school else "",  # S (18)
        property_.primary_school.inspection_year if property_.primary_school else "",  # T (19)
        property_.primary_school.inspection_summary if property_.primary_school else "",  # U (20)
        property_.secondary_school.name if property_.secondary_school else "",  # V (21)
        _fmt_dist(property_.secondary_school),              # W (22)
        _fmt_walk(property_.secondary_school),              # X (23)
        _fmt_school_link(property_.secondary_school),       # Y (24)
        property_.secondary_school.ofsted_rating if property_.secondary_school else "",  # Z (25)
        property_.secondary_school.inspection_year if property_.secondary_school else "",  # AA (26)
        property_.secondary_school.inspection_summary if property_.secondary_school else "",  # AB (27)
        property_.town_description,                         # AC (28)
        str(property_.walk_to_town_minutes) if property_.walk_to_town_minutes is not None else "",  # AD (29)
        property_.walkable_amenities,                       # AE (30)
        property_.epc_rating,                               # AF (31)
        _fmt_bus(property_.secondary_school),               # AG (32)
        property_.secondary_school.bus_route if property_.secondary_school else "",  # AH (33)
        str(property_.approx_latitude) if property_.approx_latitude is not None else "",  # AI (34)
        str(property_.approx_longitude) if property_.approx_longitude is not None else "",  # AJ (35)
        property_.approx_station_crs,                       # AK (36)
        property_.approx_station_name,                      # AL (37)
    ]


def ensure_headers(worksheet: gspread.Worksheet) -> None:
    if worksheet.row_count == 0 or not worksheet.get_all_values():
        worksheet.append_row(COLUMN_HEADERS, value_input_option="USER_ENTERED")


async def write_enriched_row(property_: EnrichedProperty, tab: str = SHEET_TAB) -> str | None:
    if not settings.sheet_id:
        logger.info("No HOUSES_SHEET_ID configured; skipping sheet write")
        return None

    client = get_client()
    if client is None:
        logger.warning("No service account credentials configured; skipping sheet write")
        return None

    try:
        sh = client.open_by_key(settings.sheet_id)
        worksheet = sh.worksheet(tab)

        ensure_headers(worksheet)
        row = _row_values(property_)
        _assert_no_user_column_writes(row)

        # Find existing row by Rightmove ID (column H, index 7). Never append duplicates.
        existing = worksheet.get_all_values()
        target_row = None
        rid = _rightmove_id(property_.url)
        RID_COL = col_index("Rightmove ID")
        if rid:
            for i, r in enumerate(existing[1:], 2):
                if len(r) > RID_COL and r[RID_COL].strip() == rid:
                    target_row = i
                    break

        if target_row:
            # Only write non-empty cells to avoid blanking user data
            cells = []
            last_col = len(row) - 1
            for j, val in enumerate(row):
                if val:
                    cl = _col_letter(j)
                    cells.append({"range": f"{cl}{target_row}", "values": [[val]]})
            if cells:
                worksheet.spreadsheet.values_batch_update(
                    {"valueInputOption": "USER_ENTERED", "data": cells}
                )
            row_url = f"https://docs.google.com/spreadsheets/d/{settings.sheet_id}/edit#gid={worksheet.id}&range=A{target_row}"
            logger.info("Updated row %d for Rightmove ID %s", target_row, rid)
        else:
            worksheet.append_row(row, value_input_option="USER_ENTERED")
            new_row_num = worksheet.row_count
            row_url = f"https://docs.google.com/spreadsheets/d/{settings.sheet_id}/edit#gid={worksheet.id}&range=A{new_row_num}"
            logger.info("Appended row for %s", property_.url)

        return row_url
    except gspread.SpreadsheetNotFound:
        logger.error("Sheet with id=%s not found. Share it with the service account email.", settings.sheet_id)
        return None
    except gspread.WorksheetNotFound:
        logger.error("Worksheet '%s' not found in sheet %s", tab, settings.sheet_id)
        return None
    except Exception:
        logger.exception("Failed to write row to Google Sheets")
        return None
