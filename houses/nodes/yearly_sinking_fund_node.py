from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode


class YearlySinkingFundNode(DerivedNode[Money]):
    def __init__(self, node_id: str, *, rightmove_price, financial_source):
        super().__init__(node_id, Money, (rightmove_price, financial_source))

    def compute(self, price: Attempt[Money], financial: Attempt[dict]) -> Attempt[Money]:
        if not price.succeeded or price.value_or_none() is None:
            return Attempt.succeeded(Money("0", "GBP"))
        price_val = price.value_or_none()
        p = price_val.amount  # Decimal
        if p == 0:
            return Attempt.succeeded(Money("0", "GBP"))
        fin = (financial.value_or_none() or {}) if financial.succeeded else {}
        rate = float(fin.get("sinking_fund_rate", 0.01))
        result = p * Decimal(str(rate))
        return Attempt.succeeded(Money(str(round(float(result), 2)), "GBP"))

    async def build_provenance(self):
        return Provenance(label="sinking_fund_formula")
