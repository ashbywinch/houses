"""gspread integration — write enriched rows to the AI_Data_Source (Bot) tab.

Server has exclusive write access to this tab. Never write to
the Properties (Human) tab.
"""

from houses.config import settings
from houses.models import EnrichedProperty


async def write_enriched_row(property_: EnrichedProperty) -> str | None:
    """Append one enriched property row to the AI_Data_Source (Bot) tab.

    Returns the row URL or None if sheets are not configured.
    """
    if not settings.sheet_id:
        return None

    # TODO: authenticate with gspread using service account
    # TODO: open workbook by sheet_id, select AI_Data_Source (Bot) tab
    # TODO: build row from EnrichedProperty fields
    # TODO: append to first empty row
    # TODO: return row link

    return None
