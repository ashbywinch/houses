from __future__ import annotations

from money import Money

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode


class TotalMonthlyHousingCostNode(DerivedNode[Money]):
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

    async def build_provenance(self):
        return Provenance(label="total_monthly_formula")
