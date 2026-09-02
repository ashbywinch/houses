"""Admin-only maintenance endpoints.

These are kept separate from ``api_router.py`` so the main HTTP layer
stays focused on the property/user API surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request

import dag.scheduler
from dag.regenerate import force_regenerate, nodes_matching
from houses.web.auth import effective_session_user

admin_router = APIRouter(prefix="/api")


@dataclass(frozen=True)
class _RegenerateReport:
    """Regenerate outcome as serialized into the HTTP response (wire shape)."""

    matched: int
    regenerated: list[dict[str, Any]]
    skipped: list[dict[str, Any]]

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict IS the boundary — wire shape owned here (coding-standards.md)
        return dict(matched=self.matched, regenerated=self.regenerated, skipped=self.skipped)

@admin_router.post("/admin/regenerate")
# lucidlint: ignore record-shape incoming request body is the caller's
# wire payload — parsed defensively at the boundary (coding-standards.md)
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
    user = effective_session_user(request)
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

    registry = dag.scheduler.get_scheduler().registered_nodes()
    matched = nodes_matching(patterns, registry.values())
    regenerated, skipped = await force_regenerate(matched)
    return _RegenerateReport(
        matched=len(matched), regenerated=regenerated, skipped=skipped
    ).to_dict()
