"""Domain model classes for the property enrichment engine.

Pure dataclasses — no DAG logic, no persistence, no enrichment.
Use pint for quantities (no units in field names) and ``money.Money``
for costs (currency is in the object, not the variable name).

Existing classes imported for convenience:
  - ``CostGroup``, ``JourneyLeg`` from ``houses.commute``
  - ``School`` from ``houses.school``

"""

from __future__ import annotations

from dataclasses import dataclass

from money import Money
from pint import Quantity as _Quantity

from houses.commute import (
    CostGroup,  # noqa: F401 — re-export
    JourneyLeg,  # noqa: F401 — re-export
)
from houses.school import School  # noqa: F401 — re-export


@dataclass(frozen=True)
class PlaceOfInterest:
    """A named place a person needs to commute to."""

    label: str
    address: str = ""
    trips_per_week: int = 1
    weeks_per_year: int = 46


@dataclass(frozen=True)
class Person:
    """A person with dependents whose commute costs are considered."""

    name: str
    has_car: bool
    is_child: bool = False
    bus_walk_penalty_minutes: int = 30
    acceptable_schools: tuple[str, ...] = ("mixed",)
    deposit_equity: Money | None = None
    places_of_interest: tuple[PlaceOfInterest, ...] = ()


@dataclass(frozen=True)
class Commute:
    """A person's commute from a property to a place of interest.

    Every field is produced by a DerivedNode — duration, daily_cost, and
    details are never assigned ad-hoc outside the DAG.

    ``details`` replaces the old ``cost_groups`` name. Callers look here
    for both legs and costs.
    """

    person: Person
    label: str
    destination: PlaceOfInterest
    duration: _Quantity
    daily_cost: Money
    mode: str = "transit"
    details: tuple[CostGroup, ...] = ()
    is_child: bool = False


@dataclass(frozen=True)
class RightmoveProperty:
    """Property data extracted from a Rightmove page (domain version)."""

    url: str
    rid: str = ""
    address: str = ""
    postcode: str = ""
    bedrooms: int | None = None
    price: float | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True)
class Property:
    """A property's assembled data (domain version, before DAG wiring)."""

    rid: str
    rightmove_property: RightmoveProperty | None = None
    address: str = ""
    postcode: str = ""
    bedrooms: int | None = None
    price: float | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True)
class Schools:
    """Primary and secondary schools near a property."""

    primary: School | None = None
    secondary: School | None = None


@dataclass(frozen=True)
class EpcRating:
    """Energy Performance Certificate rating for a property."""

    rating: str = ""
    potential_rating: str = ""
    evidence_url: str = ""


@dataclass(frozen=True)
class Walkability:
    """Walkability data for a property — town access and amenities."""

    walk_to_town_minutes: int | None = None
    amenities: str = ""
    town_description: str = ""
