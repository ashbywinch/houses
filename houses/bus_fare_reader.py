from __future__ import annotations

from houses.bus_journey import BusJourneyRegistry
from houses.services_provider import get_services


def get_bus_fare_reader() -> BusJourneyRegistry:

    svc = get_services()
    if svc.bus_fare_registry is None:
        svc.bus_fare_registry = BusJourneyRegistry()
    return svc.bus_fare_registry
