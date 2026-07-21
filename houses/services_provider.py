from __future__ import annotations

import contextvars

from houses.services import Services

_request_services: contextvars.ContextVar[Services | None] = contextvars.ContextVar("_request_services", default=None)


def get_services() -> Services:
    svc = _request_services.get()
    if svc is None:
        svc = Services()
        _request_services.set(svc)
    return svc
