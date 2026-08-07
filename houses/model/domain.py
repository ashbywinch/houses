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
from decimal import Decimal

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
    # Modes the person accepts for this commute: "transit" | "car" | "walk",
    # any combination.  Empty means unset (legacy) — the effective value is
    # derived by ``effective_acceptable_modes`` and is what routing uses.
    acceptable_modes: tuple[str, ...] = ()

    def to_provenance_value(self) -> dict:
        """JSON-safe projection for provenance display."""
        return {"label": self.label, "address": self.address, "acceptable_modes": list(self.acceptable_modes)}


@dataclass(frozen=True)
class HomeCoOwner:
    """Another person's share (%) of THIS person's declared home.

    The holder declares co-owners; the holder keeps the remainder. The
    co-owner sees it read-only on their own settings so they understand
    the sale counts toward their deposit and don't add the house again.
    """

    name: str
    share: int


@dataclass(frozen=True)
class Person:
    """A person with dependents whose commute costs are considered."""

    name: str
    has_car: bool
    is_child: bool = False
    bus_walk_penalty: _Quantity = _Quantity(30, "minute")
    acceptable_schools: tuple[str, ...] = ("mixed",)
    petrol_mpg: int = 45
    home_co_owners: tuple[HomeCoOwner, ...] = ()
    home_property_rid: str = ""
    rent_paid_monthly: Money = Money("0", "GBP")
    home_sale_price: Money = Money("0", "GBP")
    outstanding_mortgage: Money = Money("0", "GBP")
    cash_contribution: Money = Money("0", "GBP")
    life_insurance_monthly: Money = Money("0", "GBP")
    works_estimate_required: bool = False
    places_of_interest: tuple[PlaceOfInterest, ...] = ()
    email: str = ""
    is_superuser: bool = False
    # Names of adults who may edit this person's settings.  Empty means
    # unset — see ``effective_editable_by`` (self for adults, ALL adults
    # for children).  Superusers may always edit anyone.
    editable_by: tuple[str, ...] = ()
    # Whether this person is selling a home to fund the purchase.  None
    # (unset) infers from the home fields — see ``effective_selling_home``.
    # False means no current home: the deposit is cash only.
    selling_home: bool | None = None

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


# Canonical order for the all-modes set (also the UI checkbox order).
ALL_ACCEPTABLE_MODES: tuple[str, ...] = ("transit", "car", "walk")


def effective_acceptable_modes(poi: PlaceOfInterest) -> tuple[str, ...]:
    """The modes a POI is actually routed by.

    Explicit ``acceptable_modes`` always win.  An empty (unset, legacy)
    value is migrated by label rule — the plan's migration for persisted
    persons: offices default to train, out-of-London trips to car, schools
    to walk.  Anything else keeps the old all-modes behaviour rather than
    inferring acceptability for labels the rule doesn't know.
    """
    if poi.acceptable_modes:
        return tuple(poi.acceptable_modes)
    label = poi.label.casefold()
    if "school" in label or "primary" in label or "secondary" in label:
        return ("walk",)
    if "bracknell" in label or "dad" in label:
        return ("car",)
    if label in ("pimlico", "aldgate"):
        return ("transit",)
    return ALL_ACCEPTABLE_MODES


def effective_selling_home(person: Person) -> bool:
    """Whether the person is selling a home to fund the purchase.

    Explicit ``selling_home`` always wins.  Unset infers from the home
    fields: any sale price or remaining mortgage means a home is being
    sold; a person with neither (e.g. Ashby — cash deposit only) is not.
    """
    if person.selling_home is not None:
        return person.selling_home

    def _nonzero(v) -> bool:
        """Legacy persons may hold bare numbers or dicts, not Money —
        tolerate any shape without crashing on .amount."""
        if v is None:
            return False
        if isinstance(v, Money):
            return bool(v.amount)
        if isinstance(v, dict):
            return bool(v.get("amount") or v.get("value"))
        return bool(v)

    return _nonzero(person.home_sale_price) or _nonzero(person.outstanding_mortgage)


def effective_editable_by(person: Person, all_persons: list[Person]) -> tuple[str, ...]:
    """Who may edit ``person``'s settings.

    Explicit ``editable_by`` wins.  Unset defaults to the person themselves
    for adults and to ALL adults for children (a child has no login, so any
    adult guardian edits on their behalf).  Superusers may always edit
    anyone — enforced by the endpoint, not encoded here.
    """
    if person.editable_by:
        return tuple(person.editable_by)
    if person.is_child:
        return tuple(p.name for p in all_persons if not p.is_child)
    return (person.name,)


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


def joint_owner_names(persons: list) -> set[str]:
    """The people who will jointly own the new house — derived from
    current-home ownership: the holder plus everyone named as a
    co-owner (children never count). Their expenses form the headline
    monthly figure; everyone else is still a buyer (their cash counts
    in the deposit) but their monthly costs aren't the deal-breaker.

    Falls back to ALL adults when nobody holds a home (e.g. all-cash
    first-time buyers) so the figure never collapses to zero.
    """
    adults = [p for p in persons if isinstance(p, Person) and not p.is_child]
    owners = {
        p.name
        for p in adults
        if (p.home_sale_price and p.home_sale_price.amount > 0)
        or (p.outstanding_mortgage and p.outstanding_mortgage.amount > 0)
        or p.home_co_owners
    }
    for p in adults:
        owners.update(co.name for co in p.home_co_owners)
    if not owners:
        return {p.name for p in adults}
    return owners


def home_equity_contributions(persons: list) -> dict[str, Decimal]:
    """Each adult's deposit contribution from home equity, distributed by
    co-owner shares.

    Every home-holder's equity (sale − mortgage, when selling) splits by
    their ``home_co_owners`` — each co-owner gets their share, the holder
    keeps the remainder. A person can hold a home AND co-own another's;
    both count. Children never contribute. The TOTAL is unchanged —
    only the attribution is honest.
    """
    by_name = {p.name: p for p in persons if isinstance(p, Person) and not p.is_child}
    gross: dict[str, Decimal] = {}
    for p in by_name.values():
        if not effective_selling_home(p):
            continue
        sale = p.home_sale_price.amount
        mortgage = p.outstanding_mortgage.amount
        gross[p.name] = max(Decimal(0), sale - mortgage)
    out: dict[str, Decimal] = {}
    for name, p in by_name.items():
        mine = Decimal(0)
        if name in gross:
            co_sum = Decimal(sum(co.share for co in p.home_co_owners))
            mine += gross[name] * (Decimal(100) - co_sum) / Decimal(100)
        for holder, equity in gross.items():
            if holder == name:
                continue
            for co in by_name[holder].home_co_owners:
                if co.name == name:
                    mine += equity * Decimal(co.share) / Decimal(100)
        out[name] = mine
    return out
