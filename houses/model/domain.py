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

    def to_provenance_value(self) -> dict:
        """JSON-safe projection for provenance display."""
        return {"label": self.label, "address": self.address}


@dataclass(frozen=True)
class Person:
    """A person with dependents whose commute costs are considered."""

    name: str
    has_car: bool
    is_child: bool = False
    bus_walk_penalty: _Quantity = _Quantity(30, "minute")
    acceptable_schools: tuple[str, ...] = ("mixed",)
    home_sale_price: Money = Money("0", "GBP")
    outstanding_mortgage: Money = Money("0", "GBP")
    cash_contribution: Money = Money("0", "GBP")
    life_insurance_monthly: Money = Money("0", "GBP")
    works_estimate_required: bool = False
    places_of_interest: tuple[PlaceOfInterest, ...] = ()
    email: str = ""
    is_superuser: bool = False

    def to_provenance_value(self) -> dict:
        """JSON-safe projection for provenance display.

        Keeps the identity-relevant fields; money fields render through
        their canonical string form via the generic projector.
        """
        return {
            "name": self.name,
            "has_car": self.has_car,
            "is_child": self.is_child,
            "places": [p.to_provenance_value() for p in self.places_of_interest],
        }


@dataclass(frozen=True)
class Commute:
    """A person's commute from a property to a place of interest.

    Every field is produced by a DerivedNode — duration, daily_cost, and
    details are never assigned ad-hoc outside the DAG.

    ``_details`` is the stored field.  ``details`` is a property that
    guards access — it raises ``ValueError`` when the Commute is
    infeasible, catching bugs where caller code assumes a route exists
    without checking the ``infeasible`` flag first.

    ``__repr__``, ``__eq__``, and ``__hash__`` use ``_details``
    directly, so they never trigger the guard.
    """

    person: Person
    label: str
    destination: PlaceOfInterest
    duration: _Quantity
    daily_cost: Money
    mode: str = "transit"
    _details: tuple[CostGroup, ...] = ()
    is_child: bool = False
    infeasible: bool = False

    def to_provenance_value(self) -> dict:
        """JSON-safe projection for provenance display.

        Duration and cost render as their canonical string forms; the
        destination keeps its label. Full leg-by-leg details live in
        the formula, not here.
        """
        return {
            "mode": self.mode,
            "duration": str(self.duration),
            "daily_cost": str(self.daily_cost),
            "destination": self.destination.label,
        }

    @property
    def details(self) -> tuple[CostGroup, ...]:
        if self.infeasible:
            raise ValueError(
                "Cannot access route details of an infeasible Commute. "
                "Check the infeasible flag before accessing legs/costs."
            )
        return self._details


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

    walk_to_town: _Quantity | None = None
    amenities: str = ""
    town_description: str = ""
