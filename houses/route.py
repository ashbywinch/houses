"""A routing answer, projected for the orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from pint import Quantity

from houses.commute import JourneyLeg


@dataclass(frozen=True)
class Route:
    """One route as the orchestration needs it: the route's own duration and
    distance, plus its transit legs as journey legs.

    Not a ``Commute``: a commute is the assembled, priced journey (a person, a
    destination, a cost). This is what a router answered for a pair of points,
    before any of that exists."""

    duration: Quantity
    distance: Quantity | None = None
    legs: tuple[JourneyLeg, ...] = ()
