"""Per-request dependency context using contextvars.

Each getter auto-creates production defaults when the context variable
is not set (e.g. outside an HTTP request).  Tests explicitly set the
context variable to inject fakes or pre-populated state.

Usage in production (auto-created via server middleware)::

    from houses.context import get_services, get_bus_fare_reader

    svc = get_services()
    fares = get_bus_fare_reader()

Usage in tests::

    import houses.context as ctx
    from tests.helpers import make_services

    token = ctx._request_services.set(make_services())
    try:
        ...
    finally:
        ctx._request_services.reset(token)
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from houses.bus_journey import BusJourneyRegistry
    from houses.services import Services

# ── Context variables ─────────────────────────────────────────────

_request_services: contextvars.ContextVar[Services | None] = contextvars.ContextVar("_request_services", default=None)

_request_bus_fares: contextvars.ContextVar[BusJourneyRegistry | None] = contextvars.ContextVar(
    "_request_bus_fares", default=None
)

_request_sheets_client: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "_request_sheets_client", default=None
)

# ── Lazy getters with production defaults ─────────────────────────


def get_services():
    """Return the per-request Services, creating a default one if unset."""
    from houses.services import Services

    svc = _request_services.get()
    if svc is None:
        svc = Services()
        _request_services.set(svc)
    return svc


def get_bus_fare_reader():
    """Return the per-request BusJourneyRegistry, creating a default if unset."""
    from houses.bus_journey import BusJourneyRegistry

    reader = _request_bus_fares.get()
    if reader is None:
        reader = BusJourneyRegistry()
        _request_bus_fares.set(reader)
    return reader


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
