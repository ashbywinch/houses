"""One Google route, projected for the orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from houses.commute import JourneyLeg


@dataclass(frozen=True)
class GoogleRoute:
    """One Google route as the orchestration needs it: the whole route's
    duration and distance, plus its transit legs as journey legs."""

    duration_s: int
    distance_meters: int
    legs: tuple[JourneyLeg, ...] = ()
