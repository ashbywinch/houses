"""gspread integration — write enriched rows to the AI_Data_Source (Bot) tab."""

from __future__ import annotations

import json
import logging
import re

import gspread
from google.oauth2.service_account import Credentials

from houses.config import settings
from houses.models import EnrichedProperty, SchoolInfo, TransitInfo

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

DATA_TAB = "Properties Data"
VIEW_TAB = "Properties View"
COLUMN_HEADERS: list[str] = [
    "Rightmove URL",              # A  (0) — user-owned, never overwrite
    "Address",                    # B  (1) — user-owned, never overwrite
    "Postcode",                   # C  (2) — user-owned, never overwrite
    "Bedrooms",                   # D  (3) — user-owned, never overwrite
    "Price (£)",                  # E  (4) — user-owned, never overwrite
    "Actual Latitude",            # F  (5) — user-owned, never overwrite
    "Actual Longitude",           # G  (6) — user-owned, never overwrite
    "Rightmove ID",               # H  (7) — server-written stable lookup key
    "Simon London (min)",         # I  (8)
    "Simon London Cost (£)",      # J  (9)
    "Lorena London (min)",        # K  (10)
    "Lorena London Cost (£)",     # L  (11)
    "Bracknell Time (min)",       # M  (12)
    "Bracknell Cost (£)",         # N  (13)
    "Primary School",             # O  (14)
    "Primary Distance (km)",      # P  (15)
    "Primary Walk (min)",         # Q  (16)
    "Primary School Link",        # R  (17)
    "Primary Ofsted",             # S  (18)
    "Primary Inspection Year",    # T  (19)
    "Secondary School",           # U  (20)
    "Secondary Distance (km)",    # V  (21)
    "Secondary Walk (min)",       # W  (22)
    "Secondary School Link",      # X  (23)
    "Secondary Ofsted",           # Y  (24)
    "Secondary Inspection Year",  # Z  (25)
    "Area Description",           # AA (26)
    "Walk to Town (min)",         # AB (27)
    "Walkable Amenities",         # AC (28)
    "EPC Rating",                 # AD (29)
    "Secondary Bus (min)",        # AE (30)
    "Secondary Bus Route",        # AF (31)
    "Approx Latitude (est)",      # AG (32)
    "Approx Longitude (est)",     # AH (33)
    "Approx Station CRS",         # AI (34)
    "Approx Station Name",        # AJ (35)
]

# Canonical View tab headers — single source of truth. Must be imported by
# scripts/setup_sheet.py and tests/integration/test_view_formulas.py.
VIEW_HEADERS: list[str] = [
    "Listing Address",
    "Rightmove Link",
    "Rightmove ID",
    "Purchase Cost (£)",
    "EPC Rating",
    "Yearly Commute Total (£)",
    "Yearly Council Tax (£)",
    "Simon London",
    "Lorena London",
    "Bracknell Time",
    "What the Area is Like",
    "Walk to Town",
    "Walkable Amenities",
    "Primary School",
    "Primary Ofsted",
    "Primary Walk",
    "Secondary School",
    "Secondary Ofsted",
    "Secondary Walk",
    "Secondary Bus Route",
    "Group Notes / WhatsApp",
    "Ashby comments",
    "Status",
    "Primary Inspection Year",
    "Primary Inspection Summary",
    "Secondary Inspection Year",
    "Secondary Inspection Summary",
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


def named_range_name(header: str) -> str:
    """Generate a deterministic named range identifier from a column header.

    Strips special characters, CamelCases each word, prefixes with 'Data_'.
    E.g. 'Simon London (min)' → 'Data_SimonLondonMin'
    """
    clean = re.sub(r"[^a-zA-Z0-9 ]+", "", header).strip()
    words = clean.split()
    return "Data_" + "".join(w.capitalize() for w in words)


def sync_view_formulas(spreadsheet: gspread.Spreadsheet) -> None:
    """Ensure named ranges, write View formulas, and apply cell formatting.

    This is the single source of truth for View formula generation — called by
    both the production refresh-formulas command and integration tests.
    """
    ensure_named_ranges(spreadsheet)

    ws = spreadsheet.worksheet("Properties View")
    data = ws.get_all_values()
    num_rows = len(data)
    vh = {h.strip().lower(): i for i, h in enumerate(data[0])}
    vl = lambda h: col_letter(vh[h])

    KEY = "VALUE(INDEX(View_RightmoveID, ROW()))"
    LINK_URL = 'GETURL("B"&ROW())'
    NR = named_range_name
    RID = NR("Rightmove ID")

    formula_cols = {
        "rightmove id": f'=IFNA(REGEXEXTRACT({LINK_URL},"properties/(\\d+)"),XLOOKUP(INDEX(View_ListingAddress, ROW()),{NR("Address")},{RID}))',
        "purchase cost (£)": f'=XLOOKUP({KEY},{RID},{NR("Price (£)")}    )',
        "epc rating": f'=XLOOKUP({KEY},{RID},{NR("EPC Rating")}    )',
        "yearly commute total (£)": f'=LET(k,XLOOKUP({KEY},{RID},{NR("Bracknell Cost (£)")}),g,XLOOKUP({KEY},{RID},{NR("Simon London Cost (£)")}),i,XLOOKUP({KEY},{RID},{NR("Lorena London Cost (£)")}),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)))',
        "simon london": f'=LET(v,XLOOKUP({KEY},{RID},{NR("Simon London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        "lorena london": f'=LET(v,XLOOKUP({KEY},{RID},{NR("Lorena London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        "bracknell time": f'=LET(v,XLOOKUP({KEY},{RID},{NR("Bracknell Time (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        "what the area is like": f'=XLOOKUP({KEY},{RID},{NR("Area Description")})',
        "walk to town": f'=LET(v,XLOOKUP({KEY},{RID},{NR("Walk to Town (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        "walkable amenities": f'=XLOOKUP({KEY},{RID},{NR("Walkable Amenities")})',
        "primary school": f'=HYPERLINK(XLOOKUP({KEY},{RID},{NR("Primary School Link")}),XLOOKUP({KEY},{RID},{NR("Primary School")}))',
        "primary ofsted": f'=XLOOKUP({KEY},{RID},{NR("Primary Ofsted")})',
        "primary walk": f'=LET(v,XLOOKUP({KEY},{RID},{NR("Primary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        "secondary school": f'=HYPERLINK(XLOOKUP({KEY},{RID},{NR("Secondary School Link")}),XLOOKUP({KEY},{RID},{NR("Secondary School")}))',
        "secondary ofsted": f'=XLOOKUP({KEY},{RID},{NR("Secondary Ofsted")})',
        "secondary walk": f'=LET(v,XLOOKUP({KEY},{RID},{NR("Secondary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
        "secondary bus route": f'=XLOOKUP({KEY},{RID},{NR("Secondary Bus Route")})',
        "primary inspection year": f'=XLOOKUP({KEY},{RID},{NR("Primary Inspection Year")})',
        "secondary inspection year": f'=XLOOKUP({KEY},{RID},{NR("Secondary Inspection Year")})',
    }
    for header_key, formula in formula_cols.items():
        if header_key in vh:
            cl = vl(header_key)
            if num_rows > 1:
                ws.update(values=[[formula] for _ in range(num_rows - 1)],
                           range_name=f'{cl}2:{cl}{num_rows}',
                           value_input_option='USER_ENTERED')

    sid = ws._properties["sheetId"]
    headers = data[0]
    header_lookup = {h.strip().lower(): i for i, h in enumerate(headers)}
    fmt_requests = []
    for h in ["simon london", "lorena london", "bracknell time", "walk to town", "primary walk", "secondary walk"]:
        if h in header_lookup:
            ci = header_lookup[h]
            fmt_requests.append({"repeatCell": {"range": {"sheetId": sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                                  "cell": {"userEnteredFormat": {"numberFormat": {"type": "TIME", "pattern": "[h]:mm"}}},
                                  "fields": "userEnteredFormat.numberFormat"}})
    for h in ["what the area is like", "walkable amenities", "primary school", "secondary school",
              "group notes / whatsapp", "ashby comments", "primary inspection summary", "secondary inspection summary"]:
        if h in header_lookup:
            ci = header_lookup[h]
            fmt_requests.append({"repeatCell": {"range": {"sheetId": sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                                  "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                                  "fields": "userEnteredFormat.wrapStrategy"}})
    fmt_requests.append({
        "updateSheetProperties": {
            "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 4}},
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }
    })
    fmt_requests.append({
        "repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.18, "green": 0.24, "blue": 0.31},
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)",
        }
    })
    if fmt_requests:
        spreadsheet.batch_update({"requests": fmt_requests})


def ensure_named_ranges(spreadsheet: gspread.Spreadsheet) -> None:
    existing = {r["name"]: r for r in (spreadsheet.list_named_ranges() or [])}
    current_names = set()

    requests = []

    # Data tab named ranges
    ws_data = spreadsheet.worksheet(DATA_TAB)
    sid_data = ws_data._properties["sheetId"]
    for col_idx, header in enumerate(COLUMN_HEADERS):
        name = named_range_name(header)
        current_names.add(name)
        range_spec = {"sheetId": sid_data, "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1}
        if name in existing:
            rid = existing[name]["namedRangeId"]
            requests.append({"updateNamedRange": {"namedRange": {"namedRangeId": rid, "name": name, "range": range_spec},
                                                   "fields": "range"}})
        else:
            requests.append({"addNamedRange": {"namedRange": {"name": name, "range": range_spec}}})

    # View tab named ranges
    ws_view = spreadsheet.worksheet("Properties View")
    sid_view = ws_view._properties["sheetId"]
    for name, col_idx in [("View_RightmoveLink", 1), ("View_RightmoveID", 2), ("View_ListingAddress", 0)]:
        current_names.add(name)
        range_spec = {"sheetId": sid_view, "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1}
        if name in existing:
            rid = existing[name]["namedRangeId"]
            requests.append({"updateNamedRange": {"namedRange": {"namedRangeId": rid, "name": name, "range": range_spec},
                                                   "fields": "range"}})
        else:
            requests.append({"addNamedRange": {"namedRange": {"name": name, "range": range_spec}}})

    # Delete orphaned ranges (names we no longer generate)
    for name, info in existing.items():
        if (name.startswith("Data_") or name.startswith("View_")) and name not in current_names:
            requests.append({"deleteNamedRange": {"namedRangeId": info["namedRangeId"]}})

    if requests:
        spreadsheet.batch_update({"requests": requests})


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


def _row_values(property_: EnrichedProperty) -> dict[str, str]:
    """Enriched values keyed by header name. User columns are omitted — never written."""
    result: dict[str, str] = {}
    r = result
    r["Rightmove ID"] = _rightmove_id(property_.url)
    r["Simon London (min)"] = _fmt_duration(property_.simon_commute)
    r["Simon London Cost (£)"] = _fmt_cost(property_.simon_commute.daily_cost_gbp if property_.simon_commute else None)
    r["Lorena London (min)"] = _fmt_duration(property_.lorena_commute)
    r["Lorena London Cost (£)"] = _fmt_cost(property_.lorena_commute.daily_cost_gbp if property_.lorena_commute else None)
    r["Bracknell Time (min)"] = str(property_.petrol.round_trip_minutes) if property_.petrol and property_.petrol.round_trip_minutes is not None else ""
    r["Bracknell Cost (£)"] = _fmt_cost(property_.petrol.cost_gbp if property_.petrol else None)
    r["Primary School"] = property_.primary_school.name if property_.primary_school else ""
    r["Primary Distance (km)"] = _fmt_dist(property_.primary_school)
    r["Primary Walk (min)"] = _fmt_walk(property_.primary_school)
    r["Primary School Link"] = _fmt_school_link(property_.primary_school)
    r["Primary Ofsted"] = property_.primary_school.ofsted_rating if property_.primary_school else ""
    r["Primary Inspection Year"] = property_.primary_school.inspection_year if property_.primary_school else ""
    r["Secondary School"] = property_.secondary_school.name if property_.secondary_school else ""
    r["Secondary Distance (km)"] = _fmt_dist(property_.secondary_school)
    r["Secondary Walk (min)"] = _fmt_walk(property_.secondary_school)
    r["Secondary School Link"] = _fmt_school_link(property_.secondary_school)
    r["Secondary Ofsted"] = property_.secondary_school.ofsted_rating if property_.secondary_school else ""
    r["Secondary Inspection Year"] = property_.secondary_school.inspection_year if property_.secondary_school else ""
    r["Area Description"] = property_.town_description
    r["Walk to Town (min)"] = str(property_.walk_to_town_minutes) if property_.walk_to_town_minutes is not None else ""
    r["Walkable Amenities"] = property_.walkable_amenities
    r["EPC Rating"] = property_.epc_rating
    r["Secondary Bus (min)"] = _fmt_bus(property_.secondary_school)
    r["Secondary Bus Route"] = property_.secondary_school.bus_route if property_.secondary_school else ""
    r["Approx Latitude (est)"] = str(property_.approx_latitude) if property_.approx_latitude is not None else ""
    r["Approx Longitude (est)"] = str(property_.approx_longitude) if property_.approx_longitude is not None else ""
    r["Approx Station CRS"] = property_.approx_station_crs
    r["Approx Station Name"] = property_.approx_station_name
    return result


def _build_full_row(property_: EnrichedProperty) -> list[str]:
    """Build a full positional row matching COLUMN_HEADERS order, for appending new rows."""
    enriched = _row_values(property_)
    return [enriched.get(h, "") for h in COLUMN_HEADERS]


def ensure_headers(worksheet: gspread.Worksheet) -> None:
    if worksheet.row_count == 0 or not worksheet.get_all_values():
        worksheet.append_row(COLUMN_HEADERS, value_input_option="USER_ENTERED")


async def write_enriched_row(property_: EnrichedProperty, tab: str = DATA_TAB) -> str | None:
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
        enriched = _row_values(property_)
        _assert_no_user_column_writes(list(enriched.values()))

        # Find existing row by Rightmove ID (column H). Never append duplicates.
        existing = worksheet.get_all_values()
        target_row = None
        rid = _rightmove_id(property_.url)
        sheet_headers = existing[0]
        try:
            rid_col = sheet_headers.index("Rightmove ID")
        except ValueError:
            rid_col = -1
        if rid and rid_col >= 0:
            for i, r in enumerate(existing[1:], 2):
                if len(r) > rid_col and r[rid_col].strip() == rid:
                    target_row = i
                    break

        if target_row:
            # Look up each enriched value's column by header name. Never use positions.
            header_to_col = {h: i for i, h in enumerate(sheet_headers)}
            cells = []
            for name, val in enriched.items():
                if val and name in header_to_col:
                    col_idx = header_to_col[name]
                    cl = _col_letter(col_idx)
                    cells.append({"range": f"{cl}{target_row}", "values": [[val]]})
            if cells:
                worksheet.spreadsheet.values_batch_update(
                    {"valueInputOption": "USER_ENTERED", "data": cells}
                )
            row_url = f"https://docs.google.com/spreadsheets/d/{settings.sheet_id}/edit#gid={worksheet.id}&range=A{target_row}"
            logger.info("Updated row %d for Rightmove ID %s", target_row, rid)
        else:
            worksheet.append_row(_build_full_row(property_), value_input_option="USER_ENTERED")
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
