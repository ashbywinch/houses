from __future__ import annotations

import contextvars

from houses.rail_fares import RailFareRegistry

_request_rail_fares: contextvars.ContextVar[RailFareRegistry | None] = contextvars.ContextVar(
    "_request_rail_fares", default=None
)


def get_rail_fare_registry() -> RailFareRegistry:
    reg = _request_rail_fares.get()
    if reg is None:
        reg = RailFareRegistry()
        _request_rail_fares.set(reg)
    return reg
