from __future__ import annotations

from dag.attempt import Attempt, Provenance, SourceType
from dag.derived_node import DerivedNode
from dag.node import Node


class CommuteBreakdownNode(DerivedNode[dict]):
    """Aggregates commute costs across all persons and POIs."""

    def __init__(self, node_id: str, *, commute_selectors: dict[str, Node], persons_source):
        self._commute_selectors = commute_selectors
        # persons_source is always the last dep
        super().__init__(node_id, dict, tuple(commute_selectors.values()) + (persons_source,))
        self._persons_source = persons_source

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
        yearly_total = 0.0
        per_person: dict[str, dict] = {}
        selector_values = list(self._commute_selectors.values())
        for p in persons_list or []:
            person_yearly = 0.0
            amount = 0.0
            pois = p.get("places_of_interest", ()) if isinstance(p, dict) else getattr(p, "places_of_interest", ())
            name = p.get("name") if isinstance(p, dict) else getattr(p, "name", "?")
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
                daily_amount = float(daily.amount) if daily is not None else 0.0
                amount = daily_amount
                yearly_person_poi = daily_amount * poi.trips_per_week * poi.weeks_per_year
                person_yearly += yearly_person_poi
                yearly_total += yearly_person_poi
            per_person[name] = {"daily_gbp": amount, "yearly_gbp": person_yearly}
        return Attempt.succeeded(
            {
                "persons": per_person,
                "yearly_total_gbp": yearly_total,
                "formula_explanation": "Aggregated from DAG nodes",
            }
        )

    async def build_provenance(self):
        return Provenance(label="commute_breakdown", source_type=SourceType.CALC, freshness=self._attempt.created_at)
