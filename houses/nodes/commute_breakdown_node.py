from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import override

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode
from dag.node import Node


@dataclass(frozen=True)
class _CommuteEntryJson:
    """Wire shape of one per-person commute row (node_results)."""

    label: str
    trips_per_week: int
    weeks_per_year: int
    yearly_gbp: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return dict(
            label=self.label,
            trips_per_week=self.trips_per_week,
            weeks_per_year=self.weeks_per_year,
            yearly_gbp=self.yearly_gbp,
        )


@dataclass(frozen=True)
class _PersonCommuteJson:
    """Wire shape of one person's commute block (node_results)."""

    daily_gbp: str
    yearly_gbp: str
    commutes: list[_CommuteEntryJson]

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return dict(
            daily_gbp=self.daily_gbp,
            yearly_gbp=self.yearly_gbp,
            commutes=[c.to_dict() for c in self.commutes],
        )


@dataclass(frozen=True)
class _CommuteAggregateJson:
    """Wire shape of the CommuteBreakdownNode VALUE dict (node_results)."""

    persons: dict[str, dict]
    yearly_total_gbp: str
    formula_explanation: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return dict(
            persons=self.persons,
            yearly_total_gbp=self.yearly_total_gbp,
            formula_explanation=self.formula_explanation,
        )


class CommuteBreakdownNode(DerivedNode[dict]):
    """Aggregates commute costs across all persons and POIs."""

    def __init__(self, node_id: str, *, selectors: tuple[Node, ...], persons_source: Node):
        # Deps are nodes: the selector entries plus persons_source. No
        # lambda, no dict, no provider closure. Each selector write
        # signals this node through its own dep slot; the owner rewires
        # with set_deps(...) when the destination set changes.
        self._persons_source: Node = persons_source
        super().__init__(node_id, dict, (*selectors, persons_source))

    @override
    @property
    def provenance_formula(self):

        v = self._attempt.value_or_none()
        if not self._attempt.succeeded or v is None:
            return None
        lines: list[FormulaLine] = []
        for name, pv in (v.get("persons") or {}).items():
            for c in pv.get("commutes") or ():
                yearly = Decimal(str(c.get("yearly_gbp") or 0))
                trips = c.get("trips_per_week", 0)
                weeks = c.get("weeks_per_year", 0)
                freq = f"{trips}x/wk · {weeks} wks/yr"
                lines.append(FormulaLine(label=f"{name} → {c['label']} · {freq}", value=f"£{yearly:,.2f}/yr"))
        if not lines:
            return None
        return Formula(lines=lines, result=f"£{Decimal(str(v.get('yearly_total_gbp', '0'))):,.2f}/yr")

    @override
    def compute(self, *args: Attempt[dict]) -> Attempt[dict]:
        # Persons is always last (the constructor puts persons_source
        # last); the rest are the selector entries. Match each POI to
        # its selector by key: selector ids carry the key
        # (<rid>/<person>/<label>/final_fuel) or equal it (tests).
        if not args:
            return Attempt.succeeded(
                {
                    "persons": {},
                    "yearly_total_gbp": 0.0,
                    "formula_explanation": "No commute data",
                }
            )
        *selector_attempts, persons = args
        if not persons.succeeded:
            return persons
        persons_list = persons.value_or_none() or []
        selector_deps = list(self._deps[:-1])
        by_id = {d._id: a for d, a in zip(selector_deps, selector_attempts, strict=False)}
        yearly_total = Money(amount="0", currency="GBP")
        per_person: dict[str, dict] = {}
        for p in persons_list or []:
            person_yearly = Money(amount="0", currency="GBP")
            daily_amount: Money | None = None
            pois = p.get("places_of_interest", ()) if isinstance(p, dict) else getattr(p, "places_of_interest", ())
            name = p.get("name") if isinstance(p, dict) else getattr(p, "name", "?")
            if not isinstance(name, str):
                # A person entry without a usable name still needs a stable dict key.
                name = "?"
            commutes: list[_CommuteEntryJson] = []
            for poi in pois or ():
                key = f"{name}/{poi.label}"
                commute_node = next(
                    (d for d in selector_deps if d._id == key or d._id.endswith(f"/{key}/final_fuel")),
                    None,
                )
                if commute_node is None:
                    continue
                attempt = by_id.get(commute_node._id)
                if attempt is None or not attempt.succeeded:
                    # A commute that cannot be computed propagates and
                    # this node never runs. Reaching here means the
                    # selector deps and the live persons disagree — a
                    # defect. Name it loudly: skipping the entry would
                    # publish a total that is quietly missing a
                    # person's cost.
                    return Attempt.impossible(f"commute {key} has no computable result")
                val = attempt.value_or_none()
                if val is None:
                    return Attempt.impossible(f"commute {key} produced no value")
                daily = getattr(val, "daily_cost", None)
                if daily is not None:
                    daily_amount = daily
                    yearly_person_poi = daily_amount * poi.trips_per_week * poi.weeks_per_year
                    person_yearly += yearly_person_poi
                    yearly_total += yearly_person_poi
                    commutes.append(
                        _CommuteEntryJson(
                            label=poi.label,
                            trips_per_week=poi.trips_per_week,
                            weeks_per_year=poi.weeks_per_year,
                            yearly_gbp=str(yearly_person_poi.amount),
                        )
                    )
            per_person[name] = _PersonCommuteJson(
                daily_gbp=str(daily_amount.amount) if daily_amount is not None else "0",
                yearly_gbp=str(person_yearly.amount),
                commutes=commutes,
            ).to_dict()
        return Attempt.succeeded(
            _CommuteAggregateJson(
                persons=per_person,
                yearly_total_gbp=str(yearly_total.amount),
                formula_explanation="Aggregated from DAG nodes",
            ).to_dict()
        )

    @override
    async def build_provenance(self):
        """The aggregate as a human total, never the dict dump.

        The node VALUE stays the breakdown dict (the expression system
        reads yearly_total_gbp); only the provenance display value is
        swapped for the human figure.
        """
        prov = await super().build_provenance()
        v = self._attempt.value_or_none()
        if self._attempt.succeeded and isinstance(v, dict) and v.get("yearly_total_gbp") is not None:
            prov.value = f"£{Decimal(str(v['yearly_total_gbp'])):,.2f}/yr"
        return prov
