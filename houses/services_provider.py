from __future__ import annotations

import contextvars
import importlib
from typing import Any

_request_services: contextvars.ContextVar[Any] = contextvars.ContextVar("_request_services", default=None)


def get_services() -> Any:
    """Return the request-scoped Services container, constructing one on first use.

    ``houses.services`` is loaded lazily via ``importlib`` so this module stays
    a leaf: houses.location and houses.commute_router import ``get_services``
    at module top, and a top-level import of ``houses.services`` here would
    close the cycle (location → services_provider → services → location) that
    the module-level DI threading exists to break.
    """
    svc = _request_services.get()
    if svc is None:
        services_mod = importlib.import_module("houses.services")
        svc = services_mod.Services()
        _request_services.set(svc)
    return svc
