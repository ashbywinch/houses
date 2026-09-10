from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import override

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode
from dag.node import Node


class CommuteBreakdownNode(DerivedNode[dict]):
    """Aggregates commute costs across all persons and POIs."""

    # lucidlint: ignore record-shape keyed selector→node map (variable keys), not a fixed record shape
    def __init__(self, node_id: str, *, commute_selectors: Mapping[str, Node], persons_source: Node):
        # Live selectors dict — also captured by the deps closure below;
        # compute reads the attribute, the provider re-reads the dict on
        # every staleness/refresh check.  Mapping (read-only view) so
        # concrete node-typed dicts pass the type check.
        self._commute_selectors: Mapping[str, Node] = commute_selectors
        # Composition: the dep policy is a closure over the CONSTRUCTOR
        # ARGUMENTS — never over `self` state — so the base class can
        # evaluate it at any point in the node's life without touching
        # derived state. The dict is held by reference and mutated in
        # place by _on_persons_changed, so the dep set tracks the live
        # destination set (added/removed in Settings) with no rebuild
        # and no rewiring.
        super().__init__(
            node_id,
            dict,
            deps=lambda: (*commute_selectors.values(), persons_source),
        )

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
                lines.append(
                    FormulaLine(label=f"{name} → {c['label']} · {freq}", value=f"£{yearly:,.2f}/yr")
                )
        if not lines:
            return None
        return Formula(lines=lines, result=f"£{Decimal(str(v.get('yearly_total_gbp', '0'))):,.2f}/yr")

    @override
    def compute(self, *args: Attempt[dict]) -> Attempt[dict]:
        # Last arg is always persons_source, the rest are commute selectors
        if not args:
            return Attempt.succeeded(
                {
                    "persons": {},
                    "yearly_total_gbp": 0.0,
                    "formula_explanation": "No commute data",
                }
            )
        persons_attempt = args[-1]
        commute_attempts = args[:-1]

        persons_list = persons_attempt.value_or_none() if persons_attempt.succeeded else []
        yearly_total = Money(amount="0", currency="GBP")
        per_person: dict[str, dict] = {}
        selector_values = list(self._commute_selectors.values())
        for p in persons_list or []:
            person_yearly = Money(amount="0", currency="GBP")
            daily_amount: Money | None = None
            pois = p.get("places_of_interest", ()) if isinstance(p, dict) else getattr(p, "places_of_interest", ())
            name = p.get("name") if isinstance(p, dict) else getattr(p, "name", "?")
            if not isinstance(name, str):
                # A person entry without a usable name still needs a stable dict key.
                name = "?"
            commutes: list[dict] = []
            for poi in pois or ():
                key = f"{name}/{poi.label}"
                commute_node = self._commute_selectors.get(key)
                if commute_node is None:
                    continue
                idx = selector_values.index(commute_node) if commute_node in selector_values else -1
                attempt = (
                    commute_attempts[idx] if idx >= 0 and idx < len(commute_attempts) else commute_node.latest_attempt()
                )
                if not attempt.succeeded:
                    # A commute that cannot be computed propagates and this
                    # node never runs.  Reaching here means the selector map
                    # and the dependency set disagree — a defect.  Name it
                    # loudly: skipping the entry would publish a total that is
                    # quietly missing a person's cost (2026-09-10).
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
                        # lucidlint: ignore record-shape commute entry — node_results wire shape (coding-standards.md)
                        {
                            "label": poi.label,
                            "trips_per_week": poi.trips_per_week,
                            "weeks_per_year": poi.weeks_per_year,
                            "yearly_gbp": str(yearly_person_poi.amount),
                        }
                    )
# lucidlint: ignore record-shape wire-format dict — serialization boundary
            per_person[name] = {
                "daily_gbp": str(daily_amount.amount) if daily_amount is not None else "0",
                "yearly_gbp": str(person_yearly.amount),
                "commutes": commutes,
            }
        return Attempt.succeeded(
            # lucidlint: ignore record-shape the node VALUE dict — serialized to node_results (coding-standards.md)
            {
                "persons": per_person,
                "yearly_total_gbp": str(yearly_total.amount),
                "formula_explanation": "Aggregated from DAG nodes",
            }
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
