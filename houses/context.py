"""Per-request dependency context using contextvars.

Each getter auto-creates production defaults when the context variable
is not set (e.g. outside an HTTP request).  Tests explicitly set the
context variable to inject fakes or pre-populated state.
"""

from __future__ import annotations

from typing import Any

from houses.bus_fare_reader import get_bus_fare_reader
from houses.rail_fare_registry import get_rail_fare_registry
from houses.services_provider import get_services
from houses.sheets import _real_get_client


def get_sheets_client() -> Any | None:
    """Return the per-request sheets client.

    When the context variable is set (e.g. by a test fixture), returns
    that value.  Otherwise delegates to ``houses.sheets._real_get_client``
    which manages the singleton ``gspread.Client``.
    """
    client = _request_sheets_client.get()
    if client is not None:
        return client
    return _real_get_client()
