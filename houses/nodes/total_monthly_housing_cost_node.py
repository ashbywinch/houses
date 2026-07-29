from __future__ import annotations

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode
from houses.council_tax_info import CouncilTaxInfo


class TotalMonthlyHousingCostNode(DerivedNode[Money]):
    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        lines = []

        # Mortgage
        mortgage_att = self._mortgage_node.latest_attempt()
        mortgage_val = mortgage_att.value_or_none() if mortgage_att.succeeded else None
        if mortgage_val is not None:
            lines.append(FormulaLine(label="Mortgage", value=str(mortgage_val)))

        # Sinking fund — show yearly → monthly → our share
        sinking_att = self._sinking_node.latest_attempt()
        sinking_val = sinking_att.value_or_none() if sinking_att.succeeded else None
        if sinking_val is not None:
            monthly_money = sinking_val / 12
            our_share = monthly_money * 2 / 3
            lines.append(FormulaLine(label="Sinking Fund (yearly)", value=str(sinking_val)))
            lines.append(FormulaLine(label="  ÷ 12 (monthly)", value=f"{monthly_money.amount:.2f} GBP"))
            lines.append(FormulaLine(label="  × ⅔ (our share)", value=f"{our_share.amount:.2f} GBP"))
            lines.append(FormulaLine(label="Sinking Fund (monthly)", value=f"{our_share.amount:.2f} GBP"))

        # Commute
        commute_att = self._commute_node.latest_attempt()
        commute_val = commute_att.value_or_none() if commute_att.succeeded else None
        if commute_val is not None:
            lines.append(FormulaLine(label="Commute", value=str(commute_val)))

        # Council tax
        council_att = self._council_tax_node.latest_attempt()
        council_val = council_att.value_or_none() if council_att.succeeded else None
        if council_val is not None:
            lines.append(FormulaLine(label="Council Tax", value=str(council_val)))

        return Formula(lines=lines, result=str(self._attempt.value))

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
        self._mortgage_node = monthly_mortgage_node
        self._sinking_node = yearly_sinking_fund_node
        self._commute_node = commute_breakdown_node
        self._council_tax_node = council_tax_node

    def compute(
        self,
        mortgage: Attempt[Money],
        sinking: Attempt[Money],
        financial: Attempt[dict],
        commute: Attempt[dict],
        council_tax: Attempt[CouncilTaxInfo],
    ) -> Attempt[Money]:
        if mortgage.impossible:
            return Attempt.impossible(mortgage.error)

        total = Money("0", "GBP")
        if mortgage.succeeded and mortgage.value_or_none():
            total += mortgage.value_or_none()
        if sinking.succeeded and sinking.value_or_none():
            sv = sinking.value_or_none()
            total += sv / 12 * 2 / 3
        if commute.succeeded:
            cb = commute.value_or_none() or {}
            yt = cb.get("yearly_total_gbp", "0")
            if isinstance(yt, Money):
                total += yt / 12
            else:
                total += Money(str(yt), "GBP") / 12
        if council_tax.succeeded:
            ct_val = council_tax.value_or_none()
            if ct_val is not None and ct_val.yearly_cost is not None:
                total += ct_val.yearly_cost / 12
        return Attempt.succeeded(total)
