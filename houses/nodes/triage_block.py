"""The triage section both property payloads carry."""

from __future__ import annotations

from typing import Any, TypedDict


class TriageBlock(TypedDict, total=False):
    """Wire shape of the triage block."""

    favourite: Any
    dismissed: Any
    is_viewed: Any
    user_notes: Any
    triage_status: Any
