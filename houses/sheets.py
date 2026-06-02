"""gspread integration — write enriched rows to the AI_Data_Source (Bot) tab."""

import json
import logging

import gspread
from google.oauth2.service_account import Credentials

from houses.config import settings
from houses.models import EnrichedProperty, SchoolInfo, TransitInfo

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_TAB = "AI_Data_Source (Bot)"
COLUMN_HEADERS: list[str] = [
    "Rightmove URL",
    "Address",
    "Postcode",
    "Bedrooms",
    "Price (£)",
    "Simon Commute (min)",
    "Lorenas Commute (min)",
    "Bracknell Petrol (£)",
    "Primary School",
    "Primary School Distance (km)",
    "Secondary School",
    "Secondary School Distance (km)",
]

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


def _fmt_dist(s: SchoolInfo | None) -> str:
    return f"{s.distance_km:.2f}" if s and s.distance_km is not None else ""


def _row_values(property_: EnrichedProperty) -> list[str]:
    return [
        property_.url,
        property_.address,
        property_.postcode,
        str(property_.bedrooms),
        f"{property_.price:,.0f}" if property_.price else "",
        _fmt_duration(property_.simon_commute),
        _fmt_duration(property_.lorena_commute),
        f"{property_.petrol.cost_gbp:.2f}" if property_.petrol and property_.petrol.cost_gbp is not None else "",
        property_.primary_school.name if property_.primary_school else "",
        _fmt_dist(property_.primary_school),
        property_.secondary_school.name if property_.secondary_school else "",
        _fmt_dist(property_.secondary_school),
    ]


def ensure_headers(worksheet: gspread.Worksheet) -> None:
    if worksheet.row_count == 0 or not worksheet.get_all_values():
        worksheet.append_row(COLUMN_HEADERS, value_input_option="USER_ENTERED")


async def write_enriched_row(property_: EnrichedProperty) -> str | None:
    if not settings.sheet_id:
        logger.info("No HOUSES_SHEET_ID configured; skipping sheet write")
        return None

    client = get_client()
    if client is None:
        logger.warning("No service account credentials configured; skipping sheet write")
        return None

    try:
        sh = client.open_by_key(settings.sheet_id)
        worksheet = sh.worksheet(SHEET_TAB)

        ensure_headers(worksheet)
        row = _row_values(property_)
        worksheet.append_row(row, value_input_option="USER_ENTERED")

        new_row_num = worksheet.row_count
        url = f"https://docs.google.com/spreadsheets/d/{settings.sheet_id}/edit#gid={worksheet.id}&range=A{new_row_num}"
        logger.info("Appended row %d for %s", new_row_num, property_.url)
        return url
    except gspread.SpreadsheetNotFound:
        logger.error("Sheet with id=%s not found. Share it with the service account email.", settings.sheet_id)
        return None
    except gspread.WorksheetNotFound:
        logger.error("Worksheet '%s' not found in sheet %s", SHEET_TAB, settings.sheet_id)
        return None
    except Exception:
        logger.exception("Failed to write row to Google Sheets")
        return None
