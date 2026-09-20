"""Monthly sinking fund node — yearly sinking ÷ 12."""

from __future__ import annotations

from typing import override

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode
from dag.node import Node


class MonthlySinkingFundNode(DerivedNode[Money]):
    """Monthly sinking fund = yearly sinking fund ÷ 12.

    The shared-cost split (each person's share) is applied by the
    headline's group node, not here — the old ×⅔ fudge is gone.
    """

    def __init__(self, node_id: str, *, yearly_sinking_fund_node):
        self._yearly_node: Node = yearly_sinking_fund_node
        super().__init__(node_id, Money, (yearly_sinking_fund_node,))

    @override
    @property
    def provenance_formula(self) -> Formula | None:
        val = self._attempt.value_or_none()
        if not self._attempt.succeeded or val is None:
            return None
        yearly = self._yearly_node.latest_attempt().value_or_none()
        lines: list[FormulaLine] = [
            FormulaLine(label="Yearly sinking fund", value=str(yearly) if yearly is not None else "—"),
            FormulaLine(label="÷ 12 (monthly)", value=str(val)),
        ]
        return Formula(lines=lines, result=str(val))

    @override
    @staticmethod
    def compute(*dep_attempts: Attempt) -> Attempt[Money]:
        yearly = dep_attempts[0]
        if not yearly.succeeded:
            return yearly
        y = yearly.value_or_none()
        if y is None:
            return yearly
        monthly = round(y.amount / 12, 2)
        return Attempt.succeeded(Money(str(monthly), "GBP"))
