from __future__ import annotations

from money import Money

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.expression import PMT, Ref


class MonthlyMortgagePaymentNode(DerivedNode[Money]):
    """Monthly mortgage payment via PMT formula."""

    def __init__(self, node_id: str, *, mortgage_required_node, mortgage_rate_node, mortgage_term_node):
        super().__init__(node_id, Money, (mortgage_required_node, mortgage_rate_node, mortgage_term_node))

    @property
    def expression(self):
        return PMT(
            principal=Ref(self._deps[0]),
            annual_rate=Ref(self._deps[1]),
            term_years=Ref(self._deps[2]),
        )

    def compute(
        self,
        mortgage_required: Attempt[Money],
        rate: Attempt,
        term: Attempt,
    ) -> Attempt[Money]:
        return self.expression.evaluate()
