"""Sheet readers — read property data from the Data tab."""

from __future__ import annotations

import logging

from houses.config import settings
from houses.sheets import get_client

logger = logging.getLogger(__name__)

VALID_TABS = {"view", "data"}


def get_properties_data() -> list[dict[str, str]]:
    """Read all properties from the Data tab and return them as dicts."""
    client = get_client()
    if not client:
        return []
    try:
        sh = client.open_by_key(settings.sheet_id)
        ws = sh.worksheet("Properties Data")
        all_rows = ws.get_all_values()
        headers = all_rows[0]
        return [dict(zip(headers, row, strict=False)) for row in all_rows[1:] if row and row[0].strip()]
    except Exception as e:
        logger.warning("Failed to read properties data: %s", e)
        return []


def resolve_tab(tab: str) -> str:
    """Validate *tab* and return ``"Properties View"`` or ``"Properties Data"``."""
    t = tab.strip().lower()
    if t not in VALID_TABS:
        raise ValueError(f"Invalid tab '{tab}'. Must be one of: {', '.join(sorted(VALID_TABS))}")
    return "Properties View" if t == "view" else "Properties Data"
