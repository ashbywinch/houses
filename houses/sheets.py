"""gspread integration — write enriched rows to the AI_Data_Source (Bot) tab.

All cell writes MUST go through ``batch_update_cells`` so that every
range is qualified with the sheet name. This prevents accidentally
writing to the wrong tab when Google Sheets resolves bare ranges to
the first sheet.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from houses.commute import Commute, LegMode
from houses.config import settings
from houses.property import EnrichedProperty
from houses.schools import School

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

DATA_TAB = "Properties Data"
VIEW_TAB = "Properties View"
CONSTANTS_TAB = "Constants"
COLUMN_HEADERS: list[str] = [
    "Rightmove URL",  # A  (0) — user-owned, never overwrite
    "Address",  # B  (1) — user-owned, never overwrite
    "Postcode",  # C  (2) — user-owned, never overwrite
    "Bedrooms",  # D  (3) — user-owned, never overwrite
    "Price (£)",  # E  (4) — user-owned, never overwrite
    "Actual Latitude",  # F  (5) — user-owned, never overwrite
    "Actual Longitude",  # G  (6) — user-owned, never overwrite
    "Rightmove ID",  # H  (7) — server-written stable lookup key
    "Simon London (min)",  # I  (8)
    "Simon London Cost (£)",  # J  (9)
    "Simon London Route",  # K  (10)
    "Simon Parking Cost (£)",  # L  (11)
    "Lorena London (min)",  # M  (12)
    "Lorena London Cost (£)",  # N  (13)
    "Lorena London Route",  # O  (14)
    "Bracknell Time (min)",  # P  (15)
    "Bracknell Cost (£)",  # Q  (16)
    "Primary School",  # R  (17)
    "Primary Distance (km)",  # S  (18)
    "Primary Walk (min)",  # T  (19)
    "Primary School Link",  # U  (20)
    "Primary Ofsted",  # V  (21)
    "Primary Inspection Year",  # W  (22)
    "Secondary School",  # X  (23)
    "Secondary Distance (km)",  # Y  (24)
    "Secondary Walk (min)",  # Z  (25)
    "Secondary School Link",  # AA (26)
    "Secondary Ofsted",  # AB (27)
    "Secondary Inspection Year",  # AC (28)
    "Area Description",  # AD (29)
    "Walk to Town (min)",  # AE (30)
    "Walkable Amenities",  # AF (31)
    "EPC Rating",  # AG (32)
    "Council Tax Band",  # AH (33)
    "Council Tax Cost (£)",  # AI (34)
    "Secondary Bus (min)",  # AJ (35)
    "Secondary Bus Route",  # AK (36)
    "Approx Latitude (est)",  # AL (37)
    "Approx Longitude (est)",  # AM (38)
    "Best Latitude",  # AN (39) — formula: Actual if set, else Approx
    "Best Longitude",  # AO (40) — formula: Actual if set, else Approx
    "Map URL",  # AP (41) — formula: Google Maps link from Best Lat/Lng
    "Approx Station CRS",  # AQ (42)
    "Approx Station Name",  # AR (43)
    # Formula columns (server never writes these — populated by Google Sheets formulas)
    "Stamp Duty (£)",  # AS (44)
    "Net Ashby Contribution (£)",  # AT (45)
    "Mortgage Required (£)",  # AU (46)
    "Monthly Mortgage Payment (£)",  # AV (47)
    "Yearly Sinking Fund (£)",  # AW (48)
]

# Conditional formatting colors (RGB 0-1 floats for Google Sheets API)
GREEN_BG = {"red": 0.85, "green": 0.92, "blue": 0.83}
ORANGE_BG = {"red": 1.0, "green": 0.95, "blue": 0.80}
RED_BG = {"red": 0.96, "green": 0.80, "blue": 0.80}
GREY_TEXT = {"red": 0.6, "green": 0.6, "blue": 0.6}

# Canonical View tab headers — single source of truth. Must be imported by
# scripts/setup_sheet.py and tests/integration/test_view_formulas.py.
VIEW_HEADERS: list[str] = [
    "Listing Address",  # A  (0)
    "Rightmove Link",  # B  (1)
    "Map",  # C  (2)
    "Rightmove ID",  # D  (3)
    "Purchase Cost (£)",  # D  (3)
    "EPC Rating",  # E  (4)
    "",  # F  (5)  gap column
    "Simon London",  # G  (6)
    "Simon London Route",  # H  (7)
    "Lorena London",  # I  (8)
    "Lorena London Route",  # J  (9)
    "Bracknell Time",  # K  (10)
    "What the Area is Like",  # L  (11)
    "Walk to Town",  # M  (12)
    "Walkable Amenities",  # N  (13)
    "",  # O  (14) gap column
    "Primary School",  # P  (15)
    "Primary Walk",  # Q  (16)
    "Primary Ofsted",  # R  (17)
    "Primary Inspection Year",  # S  (18)
    "Secondary School",  # T  (19)
    "Secondary Walk",  # U  (20)
    "Secondary Ofsted",  # V  (21)
    "Secondary Inspection Year",  # W  (22)
    "Secondary Bus",  # X  (23)
    "Secondary Bus Route",  # Y  (24)
    "",  # Z  (25) gap column
    "Monthly Mortgage Payment (£)",  # AA (26)
    "Monthly Sinking Fund (£)",  # AB (27)
    "Monthly Life Insurance (£)",  # AC (28)
    "Monthly Commute Cost (£)",  # AD (29)
    "Monthly Council Tax (£)",  # AE (30)
    "Total Monthly Housing Cost (£)",  # AF (31)
    "",  # AG (32) gap column
    "Ashby Works Estimate (£)",  # AH (33)
    "Group Notes / WhatsApp",  # AI (34)
    "Ashby comments",  # AJ (35)
    "Design Needed",  # AK (36) — yes/no dropdown
    "Planning Needed",  # AL (37) — yes/no/yikes dropdown
    "Status",  # AM (38)
    "Status Reason",  # AN (39)
]

# View tab columns that are manual (user-entered), never written by formulas
VIEW_MANUAL_COLUMNS: frozenset[str] = frozenset(
    {
        "",
        "Rightmove Link",
        "Ashby Works Estimate (£)",
        "Group Notes / WhatsApp",
        "Ashby comments",
        "Design Needed",
        "Planning Needed",
        "Status",
        "Status Reason",
    }
)

_USER_COLUMNS = frozenset(
    {
        "Rightmove URL",
        "Address",
        "Postcode",
        "Bedrooms",
        "Price (£)",
        "Actual Latitude",
        "Actual Longitude",
    }
)

_FORMULA_COLUMNS: frozenset[str] = frozenset(
    {
        "Stamp Duty (£)",
        "Net Ashby Contribution (£)",
        "Mortgage Required (£)",
        "Monthly Mortgage Payment (£)",
        "Yearly Sinking Fund (£)",
        "Best Latitude",
        "Best Longitude",
        "Map URL",
    }
)


def col_index(header: str) -> int:
    """Return the 0-based column index for the canonical header name."""
    for i, h in enumerate(COLUMN_HEADERS):
        if h == header:
            return i
    raise ValueError(f"Column '{header}' not found in COLUMN_HEADERS")


# Index positions of user-owned columns (must never be written by the server)
_USER_COL_INDICES = frozenset(col_index(h) for h in _USER_COLUMNS)


def col_letter(i: int) -> str:
    """Convert 0-based column index to Google Sheets column letter."""
    if i < 26:
        return chr(65 + i)
    return chr(64 + i // 26) + chr(65 + i % 26)


def _col_letter(index: int) -> str:
    if index < 26:
        return chr(65 + index)
    return chr(64 + index // 26) + chr(65 + index % 26)


class Tab:
    """Wraps a gspread Worksheet, auto-qualifying all ranges with the sheet name.

    Every cell write goes through ``batch_update``, which prefixes bare
    ranges with ``'SheetName'!`` so Google Sheets never defaults to the
    wrong tab. Use ``Tab`` everywhere instead of raw ``Worksheet``.
    """

    def __init__(self, ws: gspread.Worksheet):
        self._ws = ws

    @property
    def title(self) -> str:
        return self._ws.title

    @property
    def id(self) -> int:
        return self._ws.id

    @property
    def row_count(self) -> int:
        return self._ws.row_count

    @property
    def spreadsheet(self) -> gspread.Spreadsheet:
        return self._ws.spreadsheet

    def get_all_values(self) -> list[list[str]]:
        return self._ws.get_all_values()

    def append_row(self, values: list[str], value_input_option: str = "USER_ENTERED") -> None:
        self._ws.append_row(values, value_input_option=value_input_option)

    def update_cell(self, row: int, col: int, value: str) -> None:
        self._ws.update_cell(row, col, value)

    def _qualify(self, cells: list[dict[str, Any]]) -> None:
        """Prefix bare ranges with the sheet name in-place."""
        for cell in cells:
            rng = cell.get("range", "")
            if rng and not rng.startswith("'"):
                cell["range"] = f"'{self._ws.title}'!{rng}"

    def batch_update(self, cells: list[dict[str, Any]]) -> None:
        self._qualify(cells)
        self._ws.spreadsheet.values_batch_update(
            {"valueInputOption": "USER_ENTERED", "data": cells},
        )


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


_nr = named_range_name

# Headers used in the Constants tab (display name in col A, value in col B)
CONSTANTS_HEADERS: list[str] = [
    "Constant",
    "Value",
]

# (label, value_or_formula) pairs. Label is used for Const_ named range generation,
# value_or_formula is written to column B (USER_ENTERED so formulas are evaluated).
CONSTANTS_VALUES: list[tuple[str, str]] = [
    ("Current Sale Price (£)", "0"),
    ("Outstanding Mortgage (£)", "0"),
    ("Deposit", "=B2-B3"),
    ("Gross Ashby Contribution (£)", "0"),
    ("Mortgage Rate", "0.0495"),
    ("Mortgage Term (years)", "27"),
    ("Life Insurance Monthly (£)", "0"),
    ("Sinking Fund Rate", "0.01"),
    ("Rental Income (£)", "0"),
]


def _const_range_name(header: str) -> str:
    """Generate a Const_ named range from a constant name."""
    clean = re.sub(r"[^a-zA-Z0-9 ]+", "", header).strip()
    words = clean.split()
    return "Const_" + "".join(w.capitalize() for w in words)


def _splt(price: float) -> float:
    """Standard non-first-time-buyer SDLT for England."""
    if price <= 250000:
        return 0.0
    if price <= 925000:
        return (price - 250000) * 0.05
    if price <= 1500000:
        return (price - 925000) * 0.10 + 33750.0
    return (price - 1500000) * 0.12 + 91250.0


def ensure_constants_tab(spreadsheet: gspread.Spreadsheet) -> gspread.Worksheet:
    """Create the Constants tab if missing. Never overwrites existing values."""
    try:
        ws = spreadsheet.worksheet(CONSTANTS_TAB)
        logger.info("'%s' tab already exists — leaving untouched", CONSTANTS_TAB)
        return ws
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=CONSTANTS_TAB, rows=20, cols=2)

    values = [CONSTANTS_HEADERS]
    for label, val in CONSTANTS_VALUES:
        values.append([label, val])
    ws.update(range_name="A1", values=values, value_input_option="USER_ENTERED")
    logger.info("Created '%s' tab with %d constants", CONSTANTS_TAB, len(CONSTANTS_VALUES))
    return ws


VIEW_FORMULA_COLS: dict[str, str] = {
    "listing address": f"=IFNA(INDEX({_nr('Address')},ROW()),)",
    "map": f'=LET(url,IFNA(INDEX({_nr("Map URL")},ROW()),),IF(url="","",HYPERLINK(url,"Map")))',
    "rightmove id": f"=IFNA(INDEX({_nr('Rightmove ID')},ROW()),)",
    "purchase cost (£)": f"=IFNA(INDEX({_nr('Price (£)')},ROW()),)",
    "epc rating": f"=IFNA(INDEX({_nr('EPC Rating')},ROW()),)",
    "simon london": f'=IFNA(LET(v,IFNA(INDEX({_nr("Simon London (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',  # noqa: E501
    "simon london route": f"=IFNA(INDEX({_nr('Simon London Route')},ROW()),)",
    "lorena london": f'=IFNA(LET(v,IFNA(INDEX({_nr("Lorena London (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',  # noqa: E501
    "lorena london route": f"=IFNA(INDEX({_nr('Lorena London Route')},ROW()),)",
    "bracknell time": f'=IFNA(LET(v,IFNA(INDEX({_nr("Bracknell Time (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',  # noqa: E501
    "what the area is like": f"=IFNA(INDEX({_nr('Area Description')},ROW()),)",
    "walk to town": f'=IFNA(LET(v,IFNA(INDEX({_nr("Walk to Town (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',  # noqa: E501
    "walkable amenities": f"=IFNA(INDEX({_nr('Walkable Amenities')},ROW()),)",
    "primary school": f"=HYPERLINK(IFNA(INDEX({_nr('Primary School Link')},ROW()),),IFNA(INDEX({_nr('Primary School')},ROW()),))",  # noqa: E501
    "primary ofsted": f"=IFNA(INDEX({_nr('Primary Ofsted')},ROW()),)",
    "primary walk": f'=IFNA(LET(v,IFNA(INDEX({_nr("Primary Walk (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',  # noqa: E501
    "secondary school": f"=HYPERLINK(IFNA(INDEX({_nr('Secondary School Link')},ROW()),),IFNA(INDEX({_nr('Secondary School')},ROW()),))",  # noqa: E501
    "secondary ofsted": f"=IFNA(INDEX({_nr('Secondary Ofsted')},ROW()),)",
    "secondary walk": f'=IFNA(LET(v,IFNA(INDEX({_nr("Secondary Walk (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',  # noqa: E501
    "secondary bus route": f"=IFNA(INDEX({_nr('Secondary Bus Route')},ROW()),)",
    "secondary bus": f'=IFNA(LET(v,IFNA(INDEX({_nr("Secondary Bus (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',  # noqa: E501
    "primary inspection year": f"=IFNA(INDEX({_nr('Primary Inspection Year')},ROW()),)",
    "secondary inspection year": f"=IFNA(INDEX({_nr('Secondary Inspection Year')},ROW()),)",
    # Affordability block — monthly costs only
    "monthly mortgage payment (£)": f"=IFNA(INDEX({_nr('Monthly Mortgage Payment (£)')},ROW()),)",
    "monthly sinking fund (£)": f"=IFNA(INDEX({_nr('Yearly Sinking Fund (£)')},ROW())/12*2/3,)",
    "monthly life insurance (£)": "=IFNA(Const_LifeInsuranceMonthly,)",
    "monthly commute cost (£)": f'=IFNA(LET(k,IFNA(INDEX({_nr("Bracknell Cost (£)")},ROW()),),g,IFNA(INDEX({_nr("Simon London Cost (£)")},ROW()),),i,IFNA(INDEX({_nr("Lorena London Cost (£)")},ROW()),),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)/12)),)',  # noqa: E501
    "monthly council tax (£)": f'=IFNA(LET(v,IFNA(INDEX({_nr("Council Tax Cost (£)")},ROW()),),IF(v=0,"",v/12)),)',
    "total monthly housing cost (£)": f'=IFNA(LET(mp,IFNA(INDEX({_nr("Monthly Mortgage Payment (£)")},ROW()),),sf,IFNA(INDEX({_nr("Yearly Sinking Fund (£)")},ROW())/12*2/3,),li,Const_LifeInsuranceMonthly,ct,IFNA(LET(v,IFNA(INDEX({_nr("Council Tax Cost (£)")},ROW()),),IF(v=0,"",v/12)),),comm,IFNA(LET(k,IFNA(INDEX({_nr("Bracknell Cost (£)")},ROW()),),g,IFNA(INDEX({_nr("Simon London Cost (£)")},ROW()),),i,IFNA(INDEX({_nr("Lorena London Cost (£)")},ROW()),),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)/12)),),s,IFNA(INDEX(View_Status,ROW()),),gross,IF(OR(mp="",comm="",ct=""),"",mp+IF(s="Current",0,sf)+IF(s="Current",0,li)+comm+ct),p,IF(gross="","",gross-IF(s="Current",IFNA(Const_RentalIncome,0),0)),IF(OR(p="",p=0),"",p)),)',  # noqa: E501
}

# Data tab formula columns (lowercase header -> Google Sheets formula string).
# These are never written by the server — they're formula-driven.
DATA_FORMULA_COLS: dict[str, str] = {
    "stamp duty (£)": f'=IFNA(LET(s,IFNA(INDEX(View_Status,ROW()),),p,INDEX({_nr("Price (£)")},ROW()),sd,IF(p<=250000,0,IF(p<=925000,(p-250000)*0.05,IF(p<=1500000,(p-925000)*0.1+33750,(p-1500000)*0.12+91250))),IF(s="Current",0,sd)),)',  # noqa: E501
    "net ashby contribution (£)": f'=IFNA(LET(s,IFNA(INDEX(View_Status,ROW()),),p,INDEX({_nr("Price (£)")},ROW()),na,Const_GrossAshbyContribution-IFNA(INDEX({_nr("Stamp Duty (£)")},ROW())/3,)-IFNA(INDEX(View_AshbyWorksEstimate,ROW()),),IF(s="Current",0,IF(OR(p=0,p=""),na,MIN(na,p/3)))),)',  # noqa: E501
    "mortgage required (£)": f"=IFNA(INDEX({_nr('Price (£)')},ROW()),)-Const_Deposit-IFNA(INDEX({_nr('Net Ashby Contribution (£)')},ROW()),)",  # noqa: E501
    "monthly mortgage payment (£)": f'=IFNA(IF(AND(INDEX(View_AshbyWorksEstimate,ROW())="",INDEX(View_Status,ROW())<>"Current"),,PMT(Const_MortgageRate/12,Const_MortgageTermYears*12,-IFNA(INDEX({_nr("Mortgage Required (£)")},ROW()),0))),)',  # noqa: E501
    "yearly sinking fund (£)": f"=IFNA(INDEX({_nr('Price (£)')},ROW())*Const_SinkingFundRate,)",
    "best latitude": f'=IFNA(LET(a,IFNA(INDEX({_nr("Actual Latitude")},ROW()),),IF(a<>"",a,IFNA(INDEX({_nr("Approx Latitude (est)")},ROW()),))),)',  # noqa: E501
    "best longitude": f'=IFNA(LET(a,IFNA(INDEX({_nr("Actual Longitude")},ROW()),),IF(a<>"",a,IFNA(INDEX({_nr("Approx Longitude (est)")},ROW()),))),)',  # noqa: E501
    "map url": f'=LET(lat,IFNA(INDEX({_nr("Best Latitude")},ROW()),0),lng,IFNA(INDEX({_nr("Best Longitude")},ROW()),0),IF(OR(lat=0,lng=0),"","https://www.google.com/maps?q="&lat&","&lng&"&t=k"))',  # noqa: E501
}


def sync_data_formulas(spreadsheet: gspread.Spreadsheet) -> None:
    """Write Data tab formulas for all formula-only columns (rows 2–N)."""
    ws = spreadsheet.worksheet(DATA_TAB)
    data = ws.get_all_values()
    num_rows = len(data)

    for header_key, formula in DATA_FORMULA_COLS.items():
        for col_idx, header in enumerate(COLUMN_HEADERS):
            if header.lower() == header_key:
                cl = col_letter(col_idx)
                if num_rows > 1:
                    write_rows = max(num_rows - 1, 1)
                    ws.update(
                        values=[[formula] for _ in range(write_rows)],
                        range_name=f"{cl}2:{cl}{1 + write_rows}",
                        value_input_option="USER_ENTERED",
                    )
                break


def _add_rule(
    fmt_requests: list,
    sid: int,
    header_lookup: dict,
    col_letter_fn,
    header_name: str,
    formula: str,
    bg_color: dict | None = None,
    text_color: dict | None = None,
) -> None:
    """Append a single conditional formatting rule to fmt_requests."""
    col_idx = header_lookup[header_name.lower()]
    rule = {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [
                    {"sheetId": sid, "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1, "startRowIndex": 1}
                ],
                "booleanRule": {
                    "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]},
                    "format": {},
                },
            }
        }
    }
    if bg_color:
        rule["addConditionalFormatRule"]["rule"]["booleanRule"]["format"]["backgroundColor"] = bg_color
    if text_color:
        text_fmt = rule["addConditionalFormatRule"]["rule"]["booleanRule"]["format"]
        text_fmt["textFormat"] = {"foregroundColor": text_color}
    fmt_requests.append(rule)


def _add_time_tiered(
    fmt_requests: list,
    sid: int,
    header_lookup: dict,
    col_letter_fn,
    header: str,
    green_hours: int,
    green_mins: int,
    orange_hours: int,
    orange_mins: int,
) -> None:
    """Add green/orange/red for a time column: <G:H G:M green, G:H G:M–O:H O:M orange, >O:H O:M red."""
    letter = col_letter_fn(header_lookup[header])
    _add_rule(
        fmt_requests,
        sid,
        header_lookup,
        col_letter_fn,
        header,
        f'=AND(${letter}2<>"",${letter}2<TIME({green_hours},{green_mins},0))',
        GREEN_BG,
    )  # noqa: E501
    orange_f = f'=AND(${letter}2<>"",${letter}2>=TIME({green_hours},{green_mins},0),${letter}2<=TIME({orange_hours},{orange_mins},0))'  # noqa: E501
    _add_rule(fmt_requests, sid, header_lookup, col_letter_fn, header, orange_f, ORANGE_BG)
    _add_rule(
        fmt_requests,
        sid,
        header_lookup,
        col_letter_fn,
        header,
        f'=AND(${letter}2<>"",${letter}2>TIME({orange_hours},{orange_mins},0))',
        RED_BG,
    )  # noqa: E501


def _add_numeric_tiered(
    fmt_requests: list,
    sid: int,
    header_lookup: dict,
    col_letter_fn,
    header: str,
    green_max: float,
    orange_max: float,
) -> None:
    """Add green/orange/red for a numeric column: <green_max green, green_max–orange_max orange, >orange_max red."""
    letter = col_letter_fn(header_lookup[header])
    _add_rule(
        fmt_requests,
        sid,
        header_lookup,
        col_letter_fn,
        header,
        f'=AND(${letter}2<>"",${letter}2<{green_max})',
        GREEN_BG,
    )  # noqa: E501
    orange_f = f'=AND(${letter}2<>"",${letter}2>={green_max},${letter}2<={orange_max})'
    _add_rule(fmt_requests, sid, header_lookup, col_letter_fn, header, orange_f, ORANGE_BG)
    _add_rule(
        fmt_requests, sid, header_lookup, col_letter_fn, header, f'=AND(${letter}2<>"",${letter}2>{orange_max})', RED_BG
    )  # noqa: E501


def _add_epc_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    """EPC Rating: A/B green, C/D orange, E/F/G red."""
    letter = col_letter_fn(header_lookup["epc rating"])
    _add_rule(
        fmt_requests,
        sid,
        header_lookup,
        col_letter_fn,
        "epc rating",
        f'=OR(LEFT(${letter}2,1)="A",LEFT(${letter}2,1)="B")',
        GREEN_BG,
    )  # noqa: E501
    _add_rule(
        fmt_requests,
        sid,
        header_lookup,
        col_letter_fn,
        "epc rating",
        f'=OR(LEFT(${letter}2,1)="C",LEFT(${letter}2,1)="D")',
        ORANGE_BG,
    )  # noqa: E501
    f = f'=OR(LEFT(${letter}2,1)="E",LEFT(${letter}2,1)="F",LEFT(${letter}2,1)="G")'
    _add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "epc rating", f, RED_BG)


def _add_commute_cost_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    """Yearly Commute Total: <£5k green, £5-10k orange, >£10k red."""
    _add_numeric_tiered(fmt_requests, sid, header_lookup, col_letter_fn, "yearly commute total (£)", 5000, 10000)


def _add_commute_time_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    """Simon/Lorena: <45m green, 45-75m orange, >75m red. Bracknell: <30/30-60/>60."""
    _add_time_tiered(fmt_requests, sid, header_lookup, col_letter_fn, "simon london", 0, 45, 1, 15)
    _add_time_tiered(fmt_requests, sid, header_lookup, col_letter_fn, "lorena london", 0, 45, 1, 15)
    _add_time_tiered(fmt_requests, sid, header_lookup, col_letter_fn, "bracknell time", 0, 30, 1, 0)


def _add_walk_time_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    """Walk to Town, Primary Walk, Secondary Walk, Secondary Bus: <15/15-30/>30."""
    for hdr in ["walk to town", "primary walk", "secondary walk", "secondary bus"]:
        _add_time_tiered(fmt_requests, sid, header_lookup, col_letter_fn, hdr, 0, 15, 0, 30)


def _add_ofsted_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    """Primary/Secondary Ofsted: Outstanding green, Good orange, Requires Improvement/Inadequate red."""
    for hdr in ["primary ofsted", "secondary ofsted"]:
        letter = col_letter_fn(header_lookup[hdr])
        _add_rule(fmt_requests, sid, header_lookup, col_letter_fn, hdr, f'=${letter}2="Outstanding"', GREEN_BG)
        _add_rule(fmt_requests, sid, header_lookup, col_letter_fn, hdr, f'=LEFT(${letter}2,4)="Good"', ORANGE_BG)
        f = f'=OR(LEFT(${letter}2,20)="Requires Improvement",LEFT(${letter}2,9)="Inadequate")'
        _add_rule(fmt_requests, sid, header_lookup, col_letter_fn, hdr, f, RED_BG)


def _add_inspection_year_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    """Inspection years: >=2023 green, <=2022 orange. 2-tier only."""
    for hdr in ["primary inspection year", "secondary inspection year"]:
        letter = col_letter_fn(header_lookup[hdr])
        _add_rule(
            fmt_requests,
            sid,
            header_lookup,
            col_letter_fn,
            hdr,
            f'=AND(${letter}2<>"",VALUE(${letter}2)>=2023)',
            GREEN_BG,
        )  # noqa: E501
        _add_rule(
            fmt_requests,
            sid,
            header_lookup,
            col_letter_fn,
            hdr,
            f'=AND(${letter}2<>"",VALUE(${letter}2)>0,VALUE(${letter}2)<=2022)',
            ORANGE_BG,
        )  # noqa: E501


def _add_grey_text_row_rule(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn, num_cols: int):
    """Full-row grey text when Status column is 'No'. Applied LAST so text dims but backgrounds stay."""
    status_letter = col_letter_fn(header_lookup["status"])
    fmt_requests.append(
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{"sheetId": sid, "startColumnIndex": 0, "endColumnIndex": num_cols, "startRowIndex": 1}],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": f'=${status_letter}2="No"'}],
                        },
                        "format": {"textFormat": {"foregroundColor": GREY_TEXT}},
                    },
                }
            }
        }
    )


def _add_status_data_validation(fmt_requests: list, sid: int, header_lookup: dict):
    """Add dropdown validation (No, Maybe) to the Status column."""
    status_idx = header_lookup.get("status")
    if status_idx is not None:
        fmt_requests.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sid,
                        "startColumnIndex": status_idx,
                        "endColumnIndex": status_idx + 1,
                        "startRowIndex": 1,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [
                                {"userEnteredValue": "No"},
                                {"userEnteredValue": "Maybe"},
                            ],
                        },
                        "showCustomUi": True,
                        "strict": "true",
                    },
                }
            }
        )


def _add_design_data_validation(fmt_requests: list, sid: int, header_lookup: dict):
    idx = header_lookup.get("design needed")
    if idx is not None:
        fmt_requests.append(
            {
                "setDataValidation": {
                    "range": {"sheetId": sid, "startColumnIndex": idx, "endColumnIndex": idx + 1, "startRowIndex": 1},
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": "Yes"}, {"userEnteredValue": "No"}],
                        },  # noqa: E501
                        "showCustomUi": True,
                        "strict": "true",
                    },
                }
            }
        )


def _add_planning_data_validation(fmt_requests: list, sid: int, header_lookup: dict):
    idx = header_lookup.get("planning needed")
    if idx is not None:
        fmt_requests.append(
            {
                "setDataValidation": {
                    "range": {"sheetId": sid, "startColumnIndex": idx, "endColumnIndex": idx + 1, "startRowIndex": 1},
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [
                                {"userEnteredValue": "Yes"},
                                {"userEnteredValue": "No"},
                                {"userEnteredValue": "Yikes"},
                            ],
                        },  # noqa: E501
                        "showCustomUi": True,
                        "strict": "true",
                    },
                }
            }
        )


def _add_design_color_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    idx = header_lookup.get("design needed")
    if idx is None:
        return
    letter = col_letter_fn(idx)
    _add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "design needed", f'=${letter}2="Yes"', ORANGE_BG)
    _add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "design needed", f'=${letter}2="No"', GREEN_BG)


def _add_planning_color_rules(fmt_requests: list, sid: int, header_lookup: dict, col_letter_fn):
    idx = header_lookup.get("planning needed")
    if idx is None:
        return
    letter = col_letter_fn(idx)
    _add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "planning needed", f'=${letter}2="Yes"', ORANGE_BG)
    _add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "planning needed", f'=${letter}2="No"', GREEN_BG)
    _add_rule(fmt_requests, sid, header_lookup, col_letter_fn, "planning needed", f'=${letter}2="Yikes"', RED_BG)


def _add_color_rules(fmt_requests: list, sid: int, headers: list[str]) -> None:
    """Orchestrate conditional formatting rules and Status column validation."""
    header_lookup = {h.strip().lower(): i for i, h in enumerate(headers)}
    col_letter_fn = col_letter

    _add_epc_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_commute_time_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_walk_time_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_ofsted_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_inspection_year_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_design_color_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_planning_color_rules(fmt_requests, sid, header_lookup, col_letter_fn)
    _add_grey_text_row_rule(fmt_requests, sid, header_lookup, col_letter_fn, len(headers))
    _add_status_data_validation(fmt_requests, sid, header_lookup)
    _add_design_data_validation(fmt_requests, sid, header_lookup)
    _add_planning_data_validation(fmt_requests, sid, header_lookup)


def sync_view_formulas(spreadsheet: gspread.Spreadsheet) -> None:
    """Ensure named ranges, write View formulas, and apply cell formatting.

    This is the single source of truth for View formula generation — called by
    both the production refresh-formulas command and integration tests.
    """
    ensure_named_ranges(spreadsheet)

    ws = spreadsheet.worksheet("Properties View")
    data = ws.get_all_values()
    num_rows = len(data)
    view_header_idx = {h.strip().lower(): i for i, h in enumerate(data[0])}

    for header_key, formula in VIEW_FORMULA_COLS.items():
        if header_key in view_header_idx:
            cl = col_letter(view_header_idx[header_key])
            if num_rows > 1:
                write_rows = max(num_rows - 1, 1)
                ws.update(
                    values=[[formula] for _ in range(write_rows)],
                    range_name=f"{cl}2:{cl}{1 + write_rows}",
                    value_input_option="USER_ENTEred",
                )

    sid = ws._properties["sheetId"]
    headers = data[0]
    header_lookup = {h.strip().lower(): i for i, h in enumerate(headers)}
    fmt_requests = []
    time_cols = [
        "simon london",
        "lorena london",
        "bracknell time",
        "walk to town",
        "primary walk",
        "secondary walk",
        "secondary bus",
    ]
    for h in time_cols:
        if h in header_lookup:
            ci = header_lookup[h]
            fmt_requests.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},  # noqa: E501
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "TIME", "pattern": "[h]:mm"}}},  # noqa: E501
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            )
    for h in [
        "purchase cost (£)",
        "monthly mortgage payment (£)",
        "monthly sinking fund (£)",
        "monthly life insurance (£)",
        "monthly commute cost (£)",
        "monthly council tax (£)",
        "total monthly housing cost (£)",
        "ashby works estimate (£)",
    ]:
        if h in header_lookup:
            ci = header_lookup[h]
            fmt_requests.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},  # noqa: E501
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "£#,##0.00"}}},  # noqa: E501
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            )

    # Grey text for Monthly Life Insurance (constant, visually distinct)
    life_key = "monthly life insurance (£)"
    if life_key in header_lookup:
        ci = header_lookup[life_key]
        fmt_requests.append(
            {
                "repeatCell": {
                    "range": {"sheetId": sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"foregroundColor": GREY_TEXT}}},
                    "fields": "userEnteredFormat.textFormat",
                }
            }
        )

    # Bold for Total Monthly Housing Cost
    total_key = "total monthly housing cost (£)"
    if total_key in header_lookup:
        ci = header_lookup[total_key]
        fmt_requests.append(
            {
                "repeatCell": {
                    "range": {"sheetId": sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat",
                }
            }
        )

    for h in [
        "what the area is like",
        "walkable amenities",
        "simon london route",
        "lorena london route",
        "primary school",
        "secondary school",
        "group notes / whatsapp",
        "ashby comments",
        "ashby works estimate (£)",
        "status reason",
        "primary inspection year",
        "secondary inspection year",
        "secondary bus",
    ]:
        if h in header_lookup:
            ci = header_lookup[h]
            fmt_requests.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},  # noqa: E501
                        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat.wrapStrategy",
                    }
                }
            )
    fmt_requests.append(
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 4}},
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        }
    )
    # Top-align all cells in the view tab (header + data rows)
    fmt_requests.append(
        {
            "repeatCell": {
                "range": {"sheetId": sid},
                "cell": {"userEnteredFormat": {"verticalAlignment": "TOP"}},
                "fields": "userEnteredFormat.verticalAlignment",
            }
        }
    )
    fmt_requests.append(
        {
            "repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.18, "green": 0.24, "blue": 0.31},
                        "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        }
    )
    if fmt_requests:
        spreadsheet.batch_update({"requests": fmt_requests})

    # Extended: conditional formatting rules + Status data validation
    extra_requests: list = []

    # Clear existing conditional formatting rules for the View tab
    # Must delete from highest index to lowest since batch processes in order
    try:
        sheet_data = spreadsheet.client.request(
            "get",
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet.id}",
            params={"fields": "sheets(conditionalFormats,properties.sheetId)"},
        )
        parsed = json.loads(sheet_data.content)
        for s in parsed.get("sheets", []):
            if s["properties"]["sheetId"] == sid:
                rule_count = len(s.get("conditionalFormats", []))
                for i in range(rule_count - 1, -1, -1):
                    extra_requests.append({"deleteConditionalFormatRule": {"sheetId": sid, "index": i}})
                break
    except Exception as exc:
        logger.warning("Failed to clear existing conditional formatting rules: %s", exc)

    _add_color_rules(extra_requests, sid, headers)
    if extra_requests:
        spreadsheet.batch_update({"requests": extra_requests})

    # Visual zone separators — thick right borders between column groups
    zone_boundaries = [5, 14, 25, 32]  # last column index of each zone (pre-gap)
    border_requests: list = []
    for col in zone_boundaries:
        border_requests.append(
            {
                "updateBorders": {
                    "range": {
                        "sheetId": sid,
                        "startColumnIndex": col,
                        "endColumnIndex": col + 1,
                    },
                    "right": {
                        "style": "SOLID_MEDIUM",
                        "color": {"red": 0.5, "green": 0.5, "blue": 0.5},
                    },
                }
            }
        )

        # Column groups — gap columns prevent adjacent merge.
        # Delete existing groups first to avoid accumulation.
        delete_requests: list = []
        try:
            existing_sheet = json.loads(
                spreadsheet.client.request(
                    "get",
                    f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet.id}",
                    params={"fields": "sheets(properties,columnGroups)"},
                ).content
            )
            for s in existing_sheet.get("sheets", []):
                if s["properties"]["sheetId"] == sid:
                    for cg in sorted(s.get("columnGroups", []), key=lambda x: x.get("depth", 0), reverse=True):
                        r = cg["range"]
                        delete_requests.append(
                            {
                                "deleteDimensionGroup": {
                                    "range": {
                                        "sheetId": sid,
                                        "dimension": "COLUMNS",
                                        "startIndex": r["startIndex"],
                                        "endIndex": r["endIndex"],
                                    }
                                }
                            }
                        )
                    break
            if delete_requests:
                spreadsheet.batch_update({"requests": delete_requests})
        except Exception as exc:
            logger.warning("Failed to clear column groups: %s", exc)

    gap_cols = {6, 15, 26, 33}
    zones = [
        (0, 6),
        (7, 15),
        (16, 26),
        (27, 33),
        (34, 41),
    ]
    for start, end in zones:
        border_requests.append(
            {
                "addDimensionGroup": {
                    "range": {
                        "sheetId": sid,
                        "dimension": "COLUMNS",
                        "startIndex": start,
                        "endIndex": end,
                    }
                }
            }
        )

    # Gap columns: very narrow width
    for gc in gap_cols:
        border_requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sid,
                        "dimension": "COLUMNS",
                        "startIndex": gc,
                        "endIndex": gc + 1,
                    },
                    "properties": {"pixelSize": 16},
                    "fields": "pixelSize",
                }
            }
        )

    if border_requests:
        spreadsheet.batch_update({"requests": border_requests})


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
            requests.append(
                {
                    "updateNamedRange": {
                        "namedRange": {"namedRangeId": rid, "name": name, "range": range_spec},  # noqa: E501
                        "fields": "range",
                    }
                }
            )
        else:
            requests.append({"addNamedRange": {"namedRange": {"name": name, "range": range_spec}}})

    # View tab named ranges
    ws_view = spreadsheet.worksheet("Properties View")
    sid_view = ws_view._properties["sheetId"]
    for name, col_idx in [
        ("View_RightmoveLink", 1),
        ("View_Map", 2),
        ("View_RightmoveID", 3),
        ("View_ListingAddress", 0),
        ("View_AshbyWorksEstimate", 34),
        ("View_DesignNeeded", 37),
        ("View_PlanningNeeded", 38),
        ("View_Status", 39),
    ]:
        current_names.add(name)
        range_spec = {"sheetId": sid_view, "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1}
        if name in existing:
            rid = existing[name]["namedRangeId"]
            requests.append(
                {
                    "updateNamedRange": {
                        "namedRange": {"namedRangeId": rid, "name": name, "range": range_spec},  # noqa: E501
                        "fields": "range",
                    }
                }
            )
        else:
            requests.append({"addNamedRange": {"namedRange": {"name": name, "range": range_spec}}})

    # Constants tab named ranges (single-cell ranges)
    ensure_constants_tab(spreadsheet)
    ws_const = spreadsheet.worksheet(CONSTANTS_TAB)
    sid_const = ws_const._properties["sheetId"]
    for row_idx, (label, _) in enumerate(CONSTANTS_VALUES):
        name = _const_range_name(label)
        current_names.add(name)
        range_spec = {
            "sheetId": sid_const,
            "startRowIndex": row_idx + 1,
            "endRowIndex": row_idx + 2,
            "startColumnIndex": 1,
            "endColumnIndex": 2,
        }
        if name in existing:
            rid = existing[name]["namedRangeId"]
            requests.append(
                {
                    "updateNamedRange": {
                        "namedRange": {"namedRangeId": rid, "name": name, "range": range_spec},
                        "fields": "range",
                    }
                }
            )
        else:
            requests.append({"addNamedRange": {"namedRange": {"name": name, "range": range_spec}}})

    # Delete orphaned ranges (names we no longer generate)
    for name, info in existing.items():
        prefixes = ("Data_", "View_", "Const_")
        if name.startswith(prefixes) and name not in current_names:
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


def _fmt_duration(t: Commute | None) -> str:
    return str(t.duration_minutes) if t and t.duration_minutes is not None else ""


def _fmt_cost(val: float | None) -> str:
    return f"{val:.2f}" if val else ""


def _fmt_dist(distance_km: float | None) -> str:
    return f"{distance_km:.2f}" if distance_km is not None else ""


def _fmt_walk(commute: Commute | None) -> str:
    if commute and commute.duration_minutes is not None and commute.cost_groups:
        # Only show walk time for walking commutes
        legs = commute.cost_groups[0].legs
        if legs and legs[0].mode == LegMode.WALK:
            return str(commute.duration_minutes)
    return ""


def _fmt_school_link(school: School | None) -> str:
    if school and school.urn:
        return f"https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/{school.urn}"
    return ""


def _fmt_bus(commute: Commute | None) -> str:
    if commute and commute.duration_minutes is not None and commute.cost_groups:
        legs = commute.cost_groups[0].legs
        if legs and legs[0].mode == LegMode.BUS:
            return str(commute.duration_minutes)
    return ""


def _fmt_bus_route(commute: Commute | None) -> str:
    """Extract bus route description from a commute, or empty string."""
    if commute and commute.cost_groups:
        for group in commute.cost_groups:
            for leg in group.legs:
                if leg.mode == LegMode.BUS and leg.description:
                    return leg.description
            # Fallback: summary of bus-containing groups
            for _leg, desc in zip(group.legs, group.leg_descriptions(), strict=True):
                if "bus" in desc.lower():
                    return desc
    return ""


def row_values(property_: EnrichedProperty) -> dict[str, str]:
    """Values keyed by header name. Includes both enriched and user-owned columns."""
    result: dict[str, str] = {}
    r = result
    r["Rightmove URL"] = property_.url
    r["Address"] = property_.address
    r["Postcode"] = property_.postcode
    r["Bedrooms"] = str(property_.bedrooms) if property_.bedrooms else ""
    r["Price (£)"] = str(property_.price) if property_.price else ""
    r["Rightmove ID"] = _rightmove_id(property_.url)
    r["Simon London (min)"] = _fmt_duration(property_.simon_commute)
    r["Simon London Cost (£)"] = _fmt_cost(property_.simon_commute.daily_cost_gbp if property_.simon_commute else None)
    r["Simon London Route"] = property_.simon_commute.summary() if property_.simon_commute else ""
    r["Simon Parking Cost (£)"] = _fmt_cost(
        property_.simon_commute.non_rail_cost() if property_.simon_commute else None
    )
    r["Lorena London (min)"] = _fmt_duration(property_.lorena_commute)
    r["Lorena London Cost (£)"] = _fmt_cost(
        property_.lorena_commute.daily_cost_gbp if property_.lorena_commute else None
    )  # noqa: E501
    r["Lorena London Route"] = property_.lorena_commute.summary() if property_.lorena_commute else ""
    bt = property_.petrol.duration_minutes if property_.petrol else None
    r["Bracknell Time (min)"] = str(bt) if bt is not None else ""
    r["Bracknell Cost (£)"] = _fmt_cost(property_.petrol.daily_cost_gbp if property_.petrol else None)
    r["Primary School"] = property_.primary_school.name if property_.primary_school else ""
    r["Primary Distance (km)"] = _fmt_dist(property_.primary_school_distance_km)
    r["Primary Walk (min)"] = _fmt_walk(property_.primary_school_commute)
    r["Primary School Link"] = _fmt_school_link(property_.primary_school)
    r["Primary Ofsted"] = property_.primary_school.ofsted_rating if property_.primary_school else ""
    r["Primary Inspection Year"] = property_.primary_school.inspection_year if property_.primary_school else ""
    r["Secondary School"] = property_.secondary_school.name if property_.secondary_school else ""
    r["Secondary Distance (km)"] = _fmt_dist(property_.secondary_school_distance_km)
    r["Secondary Walk (min)"] = _fmt_walk(property_.secondary_school_commute)
    r["Secondary School Link"] = _fmt_school_link(property_.secondary_school)
    r["Secondary Ofsted"] = property_.secondary_school.ofsted_rating if property_.secondary_school else ""
    r["Secondary Inspection Year"] = property_.secondary_school.inspection_year if property_.secondary_school else ""
    r["Area Description"] = property_.town_description
    r["Walk to Town (min)"] = str(property_.walk_to_town_minutes) if property_.walk_to_town_minutes is not None else ""
    r["Walkable Amenities"] = property_.walkable_amenities
    r["EPC Rating"] = property_.epc_rating
    r["Council Tax Band"] = property_.council_tax.band if property_.council_tax else ""
    r["Council Tax Cost (£)"] = _fmt_cost(property_.council_tax.yearly_cost if property_.council_tax else None)
    r["Secondary Bus (min)"] = _fmt_bus(property_.secondary_school_commute)
    r["Secondary Bus Route"] = _fmt_bus_route(property_.secondary_school_commute)
    r["Approx Latitude (est)"] = str(property_.approx_latitude) if property_.approx_latitude is not None else ""
    r["Approx Longitude (est)"] = str(property_.approx_longitude) if property_.approx_longitude is not None else ""
    r["Approx Station CRS"] = property_.approx_station_crs
    r["Approx Station Name"] = property_.approx_station_name
    return result


def _build_full_row(property_: EnrichedProperty) -> list[str]:
    """Build a full positional row matching COLUMN_HEADERS order, for appending new rows."""
    enriched = row_values(property_)
    return [enriched.get(h, "") for h in COLUMN_HEADERS]


def ensure_headers(worksheet: gspread.Worksheet) -> None:
    if worksheet.row_count == 0 or not worksheet.get_all_values():
        worksheet.append_row(COLUMN_HEADERS, value_input_option="USER_ENTEred")


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
        enriched = row_values(property_)

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
                Tab(worksheet).batch_update(cells)
            row_url = f"https://docs.google.com/spreadsheets/d/{settings.sheet_id}/edit#gid={worksheet.id}&range=A{target_row}"
            logger.info("Updated row %d for Rightmove ID %s", target_row, rid)
        else:
            worksheet.append_row(_build_full_row(property_), value_input_option="USER_ENTEred")
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
