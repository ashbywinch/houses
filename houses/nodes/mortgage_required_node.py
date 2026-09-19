from __future__ import annotations

from decimal import Decimal
from typing import override

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode
from dag.expression import Ref

_ZERO = Decimal("0")


class MortgageRequiredNode(DerivedNode[Money]):
    """Mortgage principal = Price + StampDuty + TotalWorks - TotalEquity."""

    def __init__(
        self,
        node_id: str,
        *,
        rightmove_price,
        stamp_duty,
        total_works_node,
        total_equity_node,
    ):
        super().__init__(
            node_id,
            Money,
            (rightmove_price, stamp_duty, total_works_node, total_equity_node),
        )

    @override
    @property
    def expression(self):
        return Ref(self._deps[0]) + Ref(self._deps[1]) + Ref(self._deps[2]) - Ref(self._deps[3])

    @override
    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        expr = self.expression
        lines = list(expr.to_formula_lines()) if expr is not None else []
        vals = [dep.latest_attempt().value_or_none() for dep in self._deps]

        def _fmt(v) -> str:
            return f"£{v.amount:,.2f}" if isinstance(v, Money) else str(v)

        lines.append(
            FormulaLine(
                label="Price + Stamp Duty + Works − Equity",
                value=(
                    f"{_fmt(vals[0])} + {_fmt(vals[1])} + {_fmt(vals[2])} − {_fmt(vals[3])}"
                    f" = {_fmt(self._attempt.value)}"
                ),
            )
        )
        return Formula(lines=lines, result=str(self._attempt.value))

    @override
    def compute(
        self,
        price: Attempt[Money],
        sd: Attempt[Money],
        tw: Attempt[Money],
        te: Attempt[Money],
    ) -> Attempt[Money]:
        result = self.expression.evaluate()
        # Cannot borrow less than zero
        clamped = (
            result.succeeded
            and result.value is not None
            and hasattr(result.value, "amount")
            and result.value.amount < 0
        )
        if clamped:
            return Attempt.succeeded(Money(amount="0", currency="GBP"))
        return result

