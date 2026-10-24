"""The rows a provider's JSON answer carries under a key.

Every answer class reads its rows the same way — a body that may be missing,
a key that may be missing, a value that must be a list — and differs only in
the key name. That reading is one function; the key is the caller's.
"""

from __future__ import annotations

from typing import Any


def rows_under(response: Any, key: str) -> list[Any]:
    """The list *response* carries under *key*, or ``[]`` when it carries none."""
    rows = response.get(key) if isinstance(response, dict) else None
    return rows if isinstance(rows, list) else []
