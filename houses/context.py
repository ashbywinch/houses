"""Per-request dependency context using contextvars.

Each getter auto-creates production defaults when the context variable
is not set (e.g. outside an HTTP request).  Tests explicitly set the
context variable to inject fakes or pre-populated state.
"""

from __future__ import annotations

from typing import Any

import contextvars


_request_sheets_client: contextvars.ContextVar[Any | None] = contextvars.ContextVar("_request_sheets_client", default=None)

def get_sheets_client() -> Any | None:
    """Return the per-request sheets client.

    When the context variable is set (e.g. by a test fixture), returns
    that value.  Otherwise delegates to ``houses.sheets._real_get_client``
    which manages the singleton ``gspread.Client``.
    """
    client = _request_sheets_client.get()
    if client is not None:
        return client
    from houses.sheets import _real_get_client
    return _real_get_client()
