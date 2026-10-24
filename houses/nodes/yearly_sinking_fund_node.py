from __future__ import annotations

from typing import override

from money import Money

from dag.attempt import SourceType
from dag.derived_node import DerivedNode
from dag.expression import Ref


class YearlySinkingFundNode(DerivedNode[Money]):
    """Yearly sinking fund = property_price × sinking_fund_rate."""

    provenance_source_type = SourceType.CONFIG

    def __init__(self, node_id: str, *, rightmove_price, sinking_fund_rate_node):
        super().__init__(node_id, Money, (rightmove_price, sinking_fund_rate_node))

    @override
    @property
    def expression(self):
        return Ref(self._deps[0]) * Ref(self._deps[1])


    compute = DerivedNode.compute  # the expression IS the computation — no forwarding shim
