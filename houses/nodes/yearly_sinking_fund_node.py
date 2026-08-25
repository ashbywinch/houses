from __future__ import annotations

from money import Money

from dag.attempt import Attempt, SourceType
from dag.derived_node import DerivedNode
from dag.expression import Ref

SINKING_FUND_RATE_NODE_ID = "settings/sinking_fund_rate"


class YearlySinkingFundNode(DerivedNode[Money]):
    """Yearly sinking fund = property_price × sinking_fund_rate."""

    provenance_source_type = SourceType.CONFIG

# lucidlint: ignore detached-method staticmethod would break instantiation/super()
    def __init__(self, node_id: str, *, rightmove_price, sinking_fund_rate_node):
        super().__init__(node_id, Money, (rightmove_price, sinking_fund_rate_node))

    @property
    def expression(self):
        return Ref(self._deps[0]) * Ref(self._deps[1])

# lucidlint: ignore middle-man protocol/reflected-operator requirement
    def compute(self, price: Attempt[Money], rate: Attempt) -> Attempt[Money]:
        return self.expression.evaluate()
