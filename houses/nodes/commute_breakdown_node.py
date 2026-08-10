from __future__ import annotations

from decimal import Decimal
from typing import override

from money import Money

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node


class CommuteBreakdownNode(DerivedNode[dict]):
    """Aggregates commute costs across all persons and POIs."""

    def __init__(self, node_id: str, *, commute_selectors: dict[str, Node], persons_source):
        self._commute_selectors = commute_selectors
        # persons_source is always the last dep
        super().__init__(node_id, dict, tuple(commute_selectors.values()) + (persons_source,))
        self._persons_source = persons_source

    @property
    def provenance_formula(self):
        from dag.attempt import Formula, FormulaLine

        v = self._attempt.value_or_none()
        if not self._attempt.succeeded or v is None:
            return None
        lines: list[FormulaLine] = []
        for name, pv in (v.get("persons") or {}).items():
            for c in pv.get("commutes") or ():
                yearly = Decimal(str(c.get("yearly_gbp") or 0))
                if yearly <= 0:
                    # Zero-cost commutes contribute nothing to the total —
                    # "how the total is calculated" skips them.
                    continue
                trips = c.get("trips_per_week", 0)
                weeks = c.get("weeks_per_year", 0)
                freq = f"{trips}x/wk · {weeks} wks/yr"
                lines.append(
                    FormulaLine(label=f"{name} → {c['label']} · {freq}", value=f"£{yearly:,.2f}/yr")
                )
        if not lines:
            return None
        return Formula(lines=lines, result=f"£{Decimal(str(v.get('yearly_total_gbp', '0'))):,.2f}/yr")

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
        yearly_total = Money("0", "GBP")
        per_person: dict[str, dict] = {}
        selector_values = list(self._commute_selectors.values())
        for p in persons_list or []:
            person_yearly = Money("0", "GBP")
            daily_amount: Money | None = None
            pois = p.get("places_of_interest", ()) if isinstance(p, dict) else getattr(p, "places_of_interest", ())
            name = p.get("name") if isinstance(p, dict) else getattr(p, "name", "?")
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
                    continue
                val = attempt.value_or_none()
                if not val:
                    continue
                daily = getattr(val, "daily_cost", None)
                if daily is not None:
                    daily_amount = daily
                    yearly_person_poi = daily_amount * poi.trips_per_week * poi.weeks_per_year
                    person_yearly += yearly_person_poi
                    yearly_total += yearly_person_poi
                    commutes.append(
                        {
                            "label": poi.label,
                            "trips_per_week": poi.trips_per_week,
                            "weeks_per_year": poi.weeks_per_year,
                            "yearly_gbp": str(yearly_person_poi.amount),
                        }
                    )
            per_person[name] = {
                "daily_gbp": str(daily_amount.amount) if daily_amount is not None else "0",
                "yearly_gbp": str(person_yearly.amount),
                "commutes": commutes,
            }
        return Attempt.succeeded(
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
