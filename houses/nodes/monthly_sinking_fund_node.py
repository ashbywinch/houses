"""Monthly sinking fund node — yearly sinking ÷ 12 × ⅔."""

from __future__ import annotations

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode


class MonthlySinkingFundNode(DerivedNode[Money]):
    """Monthly sinking fund = yearly sinking fund ÷ 12 × ⅔."""

    def __init__(self, node_id: str, *, yearly_sinking_fund_node):
        self._yearly_node = yearly_sinking_fund_node
        super().__init__(node_id, Money, (yearly_sinking_fund_node,))

    @property
    def provenance_formula(self) -> Formula | None:
        val = self._attempt.value_or_none()
        if not self._attempt.succeeded or val is None:
            return None
        yearly = self._yearly_node.latest_attempt().value_or_none()
        lines: list[FormulaLine] = [
            FormulaLine(label="Yearly sinking fund", value=str(yearly) if yearly is not None else "—"),
            FormulaLine(label="÷ 12 (monthly)", value=f"£{yearly.amount / 12:,.2f}" if yearly is not None else "—"),
            FormulaLine(label="× ⅔ (monthly share)", value=str(val)),
        ]
        return Formula(lines=lines, result=str(val))

    def compute(self, *dep_attempts: Attempt) -> Attempt[Money]:
        yearly = dep_attempts[0]
        if not yearly.succeeded:
            return yearly
        y = yearly.value_or_none()
        if y is None:
            return yearly
        monthly = round(y.amount / 12 * 2 / 3, 2)
        return Attempt.succeeded(Money(str(monthly), "GBP"))
