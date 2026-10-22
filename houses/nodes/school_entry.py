"""The per-stage school wrapper both property payloads carry."""

from __future__ import annotations

from typing import Any, TypedDict


class SchoolEntry(TypedDict, total=False):
    """The {school: ...} wrapper used per school stage."""

    school: Any
