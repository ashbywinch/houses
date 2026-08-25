from __future__ import annotations

from typing import override

from money import Money

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.expression import Ref
from houses.nodes.expressions import PMT


class MonthlyMortgagePaymentNode(DerivedNode[Money]):
    """Monthly mortgage payment via PMT formula."""

# lucidlint: ignore detached-method staticmethod would break instantiation/super()
    def __init__(self, node_id: str, *, mortgage_required_node, mortgage_rate_node, mortgage_term_node):
        super().__init__(node_id, Money, (mortgage_required_node, mortgage_rate_node, mortgage_term_node))

    @override
    @property
    def expression(self):
        return PMT(
            principal=Ref(self._deps[0]),
            annual_rate=Ref(self._deps[1]),
            term_years=Ref(self._deps[2]),
        )

# lucidlint: ignore middle-man protocol/reflected-operator requirement
    @override
    def compute(
        self,
        mortgage_required: Attempt[Money],
        rate: Attempt,
        term: Attempt,
    ) -> Attempt[Money]:
        return self.expression.evaluate()
