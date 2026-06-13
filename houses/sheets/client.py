"""Google Sheets client lifecycle — build, cache, and retrieve."""

from __future__ import annotations

import json
import logging

import gspread
from google.oauth2.service_account import Credentials

from houses.config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

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
    from houses.context import get_sheets_client

    return get_sheets_client()


def _real_get_client() -> gspread.Client | None:
    global _client
    if _client is None:
        _client = _build_client()
    return _client
