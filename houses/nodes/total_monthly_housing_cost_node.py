from __future__ import annotations

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode


class TotalMonthlyHousingCostNode(DerivedNode[Money]):
    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        try:
            lines = []
            deps_labels = ["Mortgage", "Sinking Fund", "Commute", "Council Tax"]
            dep_indices = [0, 1, 3, 4]
            for label, idx in zip(deps_labels, dep_indices, strict=False):
                att = self._deps[idx].latest_attempt()
                val = att.value_or_none() if att.succeeded else None
                if val is not None:
                    lines.append(FormulaLine(label=label, value=str(val)))
            return Formula(lines=lines, result=str(self._attempt.value))
        except Exception:
            return None

    def __init__(
        self,
        node_id: str,
        *,
        monthly_mortgage_node,
        yearly_sinking_fund_node,
        financial_source,
        commute_breakdown_node,
        council_tax_node,
    ):
        super().__init__(
            node_id,
            Money,
            (
                monthly_mortgage_node,
                yearly_sinking_fund_node,
                financial_source,
                commute_breakdown_node,
                council_tax_node,
            ),
        )

    def compute(
        self,
        mortgage: Attempt[Money],
        sinking: Attempt[Money],
        financial: Attempt[dict],
        commute: Attempt[dict],
        council_tax: Attempt[dict],
    ) -> Attempt[Money]:
        total = Money("0", "GBP")
        if mortgage.succeeded and mortgage.value_or_none():
            total += mortgage.value_or_none()
        if sinking.succeeded and sinking.value_or_none():
            sv = sinking.value_or_none()
            monthly_sinking = round(float(sv.amount) / 12 * 2 / 3, 2)
            total += Money(str(monthly_sinking), "GBP")
        if commute.succeeded:
            cb = commute.value_or_none() or {}
            total += Money(str(cb.get("yearly_total_gbp", 0.0) / 12), "GBP")
        if council_tax.succeeded:
            ct_val = council_tax.value_or_none() or {}
            total += Money(str(ct_val.get("yearly_cost", 0.0) / 12), "GBP")
        return Attempt.succeeded(total)
