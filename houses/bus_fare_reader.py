from __future__ import annotations

import contextvars

from houses.bus_journey import BusJourneyRegistry

_request_bus_fares: contextvars.ContextVar[BusJourneyRegistry | None] = contextvars.ContextVar(
    "_request_bus_fares", default=None
)


def get_bus_fare_reader() -> BusJourneyRegistry:
    reader = _request_bus_fares.get()
    if reader is None:
        reader = BusJourneyRegistry()
        _request_bus_fares.set(reader)
    return reader
