"""A Google TRANSIT route restricted to its bus steps."""

from __future__ import annotations

from dataclasses import dataclass

from pint import Quantity

from houses.transit_stop import TransitStop


@dataclass(frozen=True)
class BusRoute:
    """A Google TRANSIT route restricted to its BUS steps."""

    duration: Quantity
    stops: tuple[TransitStop, ...]
