from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt
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

    @property
    def expression(self):
        return Ref(self._deps[0]) + Ref(self._deps[1]) + Ref(self._deps[2]) - Ref(self._deps[3])

    def compute(
        self,
        price: Attempt[Money],
        sd: Attempt[Money],
        tw: Attempt[Money],
        te: Attempt[Money],
    ) -> Attempt[Money]:
        result = self.expression.evaluate()
        # Cannot borrow less than zero
        if result.succeeded and result.value is not None:
            if hasattr(result.value, "amount") and result.value.amount < 0:
                return Attempt.succeeded(Money("0", "GBP"))
        return result
