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


@admin_router.post("/admin/regenerate")
async def regenerate_nodes(body: dict, request: Request):
    """Force-recompute DerivedNodes whose persisted results are stale in
    CODE, not by timestamp — e.g. after a computation change (the A3
    council-tax fallback). Superuser-only.

    ``patterns`` is a list of node-id patterns where ``*`` matches any
    run of characters (``["*/council_tax"]`` regenerates every
    property's council-tax node; a pattern without ``*`` is an exact
    id). Matched input nodes have no computation and are reported as
    skipped. The scheduler cascade is drained before responding, so
    dependents (e.g. total monthly cost) are recomputed too.
    """
    from dag.regenerate import force_regenerate, nodes_matching
    from dag.scheduler import _get_scheduler
    from houses.web.auth import get_session_user

    user = get_session_user(request)
    if not user or not user.get("is_superuser"):
        raise HTTPException(status_code=403, detail="Superuser access required")

    patterns = body.get("patterns")
    if not isinstance(patterns, list) or not patterns or not all(
        isinstance(p, str) and p.strip() for p in patterns
    ):
        raise HTTPException(
            status_code=422,
            detail='patterns required: a list of node-id patterns, e.g. ["*/council_tax"]',
        )

    registry = _get_scheduler().registered_nodes()
    matched = nodes_matching(patterns, registry.values())
    regenerated, skipped = await force_regenerate(matched)
    return {"matched": len(matched), "regenerated": regenerated, "skipped": skipped}
