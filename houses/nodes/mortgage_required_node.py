from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode

_ZERO = Decimal("0")


class MortgageRequiredNode(DerivedNode[Money]):
    """Mortgage principal = Price + StampDuty + TotalWorks - TotalEquity."""

    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        lines = [
            FormulaLine(
                label="Mortgage Required", value=str(self._attempt.value)
            ),
        ]
        return Formula(lines=lines, result=str(self._attempt.value))

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

    def compute(
        self,
        price: Attempt[Money],
        sd: Attempt[Money],
        tw: Attempt[Money],
        te: Attempt[Money],
    ) -> Attempt[Money]:
        self._assert_deps_succeeded(
            price=price,
            sd=sd,
            tw=tw,
            te=te,
        )

        p = Decimal(price.value_or_none().amount)
        sdv = Decimal(sd.value_or_none().amount)
        w = Decimal(tw.value_or_none().amount)
        e = Decimal(te.value_or_none().amount)

        result = max(_ZERO, p + sdv + w - e)
        return Attempt.succeeded(Money(str(result), "GBP"))
