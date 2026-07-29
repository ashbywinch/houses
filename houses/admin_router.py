"""Admin-only endpoints that interact directly with the Google Sheet.

These are kept separate from ``api_router.py`` to enforce the layering
rule that the main HTTP layer (``houses/web/*.py``) must not import from
``houses/sheets/``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from houses.nodes.bootstrap import load_property_nodes_from_rows
from houses.property_registry import list_properties as list_registry_properties

admin_router = APIRouter(prefix="/api")


async def seed_properties():
    """Seed the property registry from the Google Sheet on cold start."""
    from houses.sheets.reader import get_properties_data

    rows = get_properties_data()
    count = load_property_nodes_from_rows(rows)
    return {"seeded": count, "total": len(list_registry_properties())}


@admin_router.post("/admin/reseed")
async def reseed_from_sheet(request: Request):
    """Re-seed the property registry from the Google Sheet.

    Superuser-only.  This is intentionally NOT automatic on server start —
    auto-reloads from code changes should not re-read the sheet.  Call this
    endpoint explicitly after seeding the sheet with new properties.
    """
    from houses.sheets.reader import get_properties_data
    from houses.web.auth import get_session_user

    user = get_session_user(request)
    if not user or not user.get("is_superuser"):
        raise HTTPException(status_code=403, detail="Superuser access required")

    rows = get_properties_data()
    load_property_nodes_from_rows(rows)
    return {"status": "ok"}
