from __future__ import annotations

from decimal import Decimal
from typing import override

from money import Money

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.expression import Conditional, Literal
from dag.node import Node
from houses.nodes.expressions import TaxTier, TieredRate


class StampDutyNode(DerivedNode[Money]):
    """Computes Stamp Duty Land Tax from the property price.

    Returns £0 when Status is "Current" (owner-occupied — no purchase).
    """

    def __init__(self, node_id: str, *, rightmove_price, status_node=None):
        self._price_node: Node = rightmove_price
        self._status_node: Node | None = status_node
        deps = [rightmove_price]
        names = ["price"]
        if status_node is not None:
            deps.append(status_node)
            names.append("status")
        super().__init__(node_id, Money, tuple(deps), dep_names=tuple(names))

    @property
    @override
    def expression(self):
        return Conditional(
            predicate=lambda: (
                ((self._status_node.latest_attempt().value_or_none() or "") if self._status_node is not None else "")
                .strip()
                .lower()
                == "current"
            ),
            if_true=Literal(Money(amount="0", currency="GBP")),
            if_false=TieredRate(
                self._price_node,
                tiers=[
                    TaxTier(rate_from=0, rate_to=250000, rate=0),
                    TaxTier(rate_from=250000, rate_to=925000, rate=Decimal("0.05")),
                    TaxTier(rate_from=925000, rate_to=1500000, rate=Decimal("0.10")),
                    TaxTier(rate_from=1500000, rate_to=None, rate=Decimal("0.12")),
                ],
                description="Stamp Duty Land Tax: 0% up to £250k, "
                "5% on £250k–£925k, 10% on £925k–£1.5M, 12% above £1.5M",
            ),
            description="Stamp Duty is a one-off government tax on property purchases.",
        )

    @override
    def _get_active_deps(self):
        return (self._price_node, self._status_node) if self._status_node is not None else (self._price_node,)

    @override
# lucidlint: ignore middle-man protocol/reflected-operator requirement
    def compute(
        self,
        price: Attempt[Money],
        status: Attempt[str] | None = None,
    ) -> Attempt[Money]:
        return self.expression.evaluate()
