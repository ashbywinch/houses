from __future__ import annotations

from houses.rail_fares import RailFareRegistry
from houses.services_provider import get_services


def get_rail_fare_registry() -> RailFareRegistry:

    svc = get_services()
    if svc.rail_fare_registry is None:
        svc.rail_fare_registry = RailFareRegistry()
    return svc.rail_fare_registry
