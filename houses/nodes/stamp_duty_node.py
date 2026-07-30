from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.expression import Conditional, Literal, TieredRate


class StampDutyNode(DerivedNode[Money]):
    """Computes Stamp Duty Land Tax from the property price.

    Returns £0 when Status is "Current" (owner-occupied — no purchase).
    """

    def __init__(self, node_id: str, *, rightmove_price, status_node=None):
        self._price_node = rightmove_price
        self._status_node = status_node
        deps = [rightmove_price]
        if status_node is not None:
            deps.append(status_node)
        super().__init__(node_id, Money, tuple(deps))

    @property
    def expression(self):
        return Conditional(
            predicate=lambda: (
                self._status_node.latest_attempt().value_or_none() or ""
            ).strip().lower()
            == "current",
            if_true=Literal(Money("0", "GBP")),
            if_false=TieredRate(
                self._price_node,
                tiers=[
                    (0, 250000, 0),
                    (250000, 925000, Decimal("0.05")),
                    (925000, 1500000, Decimal("0.10")),
                    (1500000, None, Decimal("0.12")),
                ],
                description="Stamp Duty Land Tax: 0% up to £250k, "
                "5% on £250k–£925k, 10% on £925k–£1.5M, 12% above £1.5M",
            ),
            description="Stamp Duty is a one-off government tax on property purchases.",
        )

    def _get_active_deps(self):
        if self._status_node is not None:
            return (self._price_node, self._status_node)
        return (self._price_node,)

    def compute(
        self,
        price: Attempt[Money],
        status: Attempt[str] | None = None,
    ) -> Attempt[Money]:
        return self.expression.evaluate()
