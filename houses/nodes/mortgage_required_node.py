from __future__ import annotations

from decimal import Decimal
from typing import override

from money import Money

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.expression import Ref

_ZERO = Decimal("0")


class MortgageRequiredNode(DerivedNode[Money]):
    """Mortgage principal = Price + StampDuty + TotalWorks - TotalEquity."""

# lucidlint: ignore detached-method staticmethod would break instantiation/super()
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
