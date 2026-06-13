"""Row — schema and value mapping for the Data tab row.

The ``Row`` class owns:
* Column header definitions (``HEADERS``)
* Lookup helpers (``index_of``, ``letter_of``, ``is_user_column``)
* Domain-to-sheet mapping (``from_property``, ``to_list``)
* Sheet write orchestration (``write_enriched_row``)
"""

from __future__ import annotations

import logging
import re
from typing import ClassVar

import gspread
from money import Money

from houses.commute import Commute, LegMode
from houses.config import settings
from houses.property import EnrichedProperty
from houses.schools import School
from houses.sheets.tab import Tab

logger = logging.getLogger(__name__)

# ── Tab name constants ──────────────────────────────────────────────────

DATA_TAB = "Properties Data"
VIEW_TAB = "Properties View"
CONSTANTS_TAB = "Constants"


# ── Column schema ───────────────────────────────────────────────────────


class Row:
    """Schema and value mapping for a row in the Properties Data tab.

    Class-level methods provide column lookup and domain-to-sheet conversion.
    """

    HEADERS: ClassVar[list[str]] = [
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

    _USER_COLUMNS: ClassVar[frozenset[str]] = frozenset(
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

    _FORMULA_COLUMNS: ClassVar[frozenset[str]] = frozenset(
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

    _RIGHTMOVE_ID_RE: ClassVar[re.Pattern] = re.compile(r"properties/(\d+)")

    # ── Column lookup ───────────────────────────────────────────────

    @classmethod
    def index_of(cls, header: str) -> int:
        """Return the 0-based column index for the canonical header name."""
        for i, h in enumerate(cls.HEADERS):
            if h == header:
                return i
        raise ValueError(f"Column '{header}' not found in COLUMN_HEADERS")

    @classmethod
    def letter_of(cls, index: int) -> str:
        """Convert 0-based column index to Google Sheets column letter."""
        if index < 26:
            return chr(65 + index)
        return chr(64 + index // 26) + chr(65 + index % 26)

    @classmethod
    def is_user_column(cls, header: str) -> bool:
        """Return True if *header* is a user-owned column (never overwritten by the server)."""
        return header in cls._USER_COLUMNS

    @classmethod
    def is_formula_column(cls, header: str) -> bool:
        """Return True if *header* is a formula-only column (never written by the server)."""
        return header in cls._FORMULA_COLUMNS

    @classmethod
    def rightmove_id(cls, url_or_text: str) -> str:
        """Extract the numeric Rightmove property ID from a URL or text."""
        m = cls._RIGHTMOVE_ID_RE.search(url_or_text)
        if m:
            return m.group(1)
        m = re.search(r"(\d{8,})", url_or_text)
        return m.group(1) if m else ""

    # ── Value formatting helpers ────────────────────────────────────

    @classmethod
    def _fmt_duration(cls, t: Commute | None) -> str:
        return str(t.duration_minutes) if t and t.duration_minutes is not None else ""

    @classmethod
    def _fmt_cost(cls, val: Money | float | None) -> str:
        """String representation for a cost value.

        Extracts the amount from ``Money`` objects; passes ``float`` through
        as-is (backward compat).  ``None`` returns ``""``.
        """
        if val is None:
            return ""
        if isinstance(val, Money):
            return str(val.amount)
        return str(val)

    @classmethod
    def _fmt_dist(cls, distance_km: float | None) -> str:
        return f"{distance_km:.2f}" if distance_km is not None else ""

    @classmethod
    def _fmt_walk(cls, commute: Commute | None) -> str:
        if commute and commute.duration_minutes is not None and commute.cost_groups:
            legs = commute.cost_groups[0].legs
            if legs and legs[0].mode == LegMode.WALK:
                return str(commute.duration_minutes)
        return ""

    @classmethod
    def _fmt_school_link(cls, school: School | None) -> str:
        if school and school.urn:
            return f"https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/{school.urn}"
        return ""

    @classmethod
    def _fmt_bus(cls, commute: Commute | None) -> str:
        if commute and commute.duration_minutes is not None and commute.cost_groups:
            legs = commute.cost_groups[0].legs
            if legs and legs[0].mode == LegMode.BUS:
                return str(commute.duration_minutes)
        return ""

    @classmethod
    def _fmt_bus_route(cls, commute: Commute | None) -> str:
        """Extract bus route description from a commute, or empty string."""
        if commute and commute.cost_groups:
            for group in commute.cost_groups:
                for leg in group.legs:
                    if leg.mode == LegMode.BUS:
                        return leg.line_name or "bus"
                # Fallback: summary of bus-containing groups
                for _leg, desc in zip(group.legs, group.leg_descriptions(), strict=True):
                    if "bus" in desc.lower():
                        return desc
        return ""

    # ── Domain-to-sheet mapping ─────────────────────────────────────

    @classmethod
    def from_property(cls, property_: EnrichedProperty) -> dict[str, str]:
        """Build a header→value dict from an enriched property.

        Returns values keyed by header name, including both enriched
        and user-owned columns.
        """
        result: dict[str, str] = {}
        r = result
        r["Rightmove URL"] = property_.url
        r["Address"] = property_.address
        r["Postcode"] = property_.postcode
        r["Bedrooms"] = str(property_.bedrooms) if property_.bedrooms else ""
        r["Price (£)"] = str(property_.price) if property_.price else ""
        r["Rightmove ID"] = cls.rightmove_id(property_.url)
        r["Simon London (min)"] = cls._fmt_duration(property_.simon_commute)
        r["Simon London Cost (£)"] = cls._fmt_cost(
            property_.simon_commute.daily_cost_gbp if property_.simon_commute else None
        )
        r["Simon London Route"] = property_.simon_commute.summary() if property_.simon_commute else ""
        r["Simon Parking Cost (£)"] = cls._fmt_cost(
            property_.simon_commute.non_rail_cost() if property_.simon_commute else None
        )
        r["Lorena London (min)"] = cls._fmt_duration(property_.lorena_commute)
        r["Lorena London Cost (£)"] = cls._fmt_cost(
            property_.lorena_commute.daily_cost_gbp if property_.lorena_commute else None
        )
        r["Lorena London Route"] = property_.lorena_commute.summary() if property_.lorena_commute else ""
        bt = property_.petrol.duration_minutes if property_.petrol else None
        r["Bracknell Time (min)"] = str(bt) if bt is not None else ""
        r["Bracknell Cost (£)"] = cls._fmt_cost(property_.petrol.daily_cost_gbp if property_.petrol else None)
        r["Primary School"] = property_.primary_school.name if property_.primary_school else ""
        r["Primary Distance (km)"] = cls._fmt_dist(property_.primary_school_distance_km)
        r["Primary Walk (min)"] = cls._fmt_walk(property_.primary_school_commute)
        r["Primary School Link"] = cls._fmt_school_link(property_.primary_school)
        r["Primary Ofsted"] = property_.primary_school.ofsted_rating if property_.primary_school else ""
        r["Primary Inspection Year"] = property_.primary_school.inspection_year if property_.primary_school else ""
        r["Secondary School"] = property_.secondary_school.name if property_.secondary_school else ""
        r["Secondary Distance (km)"] = cls._fmt_dist(property_.secondary_school_distance_km)
        r["Secondary Walk (min)"] = cls._fmt_walk(property_.secondary_school_commute)
        r["Secondary School Link"] = cls._fmt_school_link(property_.secondary_school)
        r["Secondary Ofsted"] = property_.secondary_school.ofsted_rating if property_.secondary_school else ""
        r["Secondary Inspection Year"] = (
            property_.secondary_school.inspection_year if property_.secondary_school else ""
        )
        r["Area Description"] = property_.town_description
        r["Walk to Town (min)"] = (
            str(property_.walk_to_town_minutes) if property_.walk_to_town_minutes is not None else ""
        )
        r["Walkable Amenities"] = property_.walkable_amenities
        r["EPC Rating"] = property_.epc_rating
        r["Council Tax Band"] = property_.council_tax.band if property_.council_tax else ""
        r["Council Tax Cost (£)"] = cls._fmt_cost(property_.council_tax.yearly_cost if property_.council_tax else None)
        r["Secondary Bus (min)"] = cls._fmt_bus(property_.secondary_school_commute)
        r["Secondary Bus Route"] = cls._fmt_bus_route(property_.secondary_school_commute)
        r["Approx Latitude (est)"] = str(property_.approx_latitude) if property_.approx_latitude is not None else ""
        r["Approx Longitude (est)"] = str(property_.approx_longitude) if property_.approx_longitude is not None else ""
        r["Approx Station CRS"] = property_.approx_station_crs
        r["Approx Station Name"] = property_.approx_station_name
        return result

    @classmethod
    def to_list(cls, property_: EnrichedProperty) -> list[str]:
        """Build a full positional row matching HEADERS order, for appending new rows."""
        enriched = cls.from_property(property_)
        return [enriched.get(h, "") for h in cls.HEADERS]


# ── Sheet-level operations ──────────────────────────────────────────────


def ensure_headers(worksheet: gspread.Worksheet) -> None:
    """Write column headers to a worksheet if it's empty."""
    if worksheet.row_count == 0 or not worksheet.get_all_values():
        worksheet.append_row(Row.HEADERS, value_input_option="USER_ENTEred")


async def write_enriched_row(property_: EnrichedProperty, tab: str = DATA_TAB) -> str | None:
    """Write an enriched property to the sheet, updating existing rows or appending new ones.

    Returns a URL to the written row, or ``None`` if the write was skipped.
    """
    if not settings.sheet_id:
        logger.info("No HOUSES_SHEET_ID configured; skipping sheet write")
        return None

    from houses.sheets.client import get_client as _get_client

    client = _get_client()
    if client is None:
        logger.warning("No service account credentials configured; skipping sheet write")
        return None

    try:
        sh = client.open_by_key(settings.sheet_id)
        worksheet = sh.worksheet(tab)

        ensure_headers(worksheet)
        enriched = Row.from_property(property_)

        # Find existing row by Rightmove ID (column H). Never append duplicates.
        existing = worksheet.get_all_values()
        target_row = None
        rid = Row.rightmove_id(property_.url)
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
                    cl = Row.letter_of(col_idx)
                    cells.append({"range": f"{cl}{target_row}", "values": [[val]]})
            if cells:
                Tab(worksheet).batch_update(cells)
            row_url = f"https://docs.google.com/spreadsheets/d/{settings.sheet_id}/edit#gid={worksheet.id}&range=A{target_row}"
            logger.info("Updated row %d for Rightmove ID %s", target_row, rid)
        else:
            worksheet.append_row(Row.to_list(property_), value_input_option="USER_ENTEred")
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
