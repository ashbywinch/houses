from __future__ import annotations

from money import Money

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode


class StampDutyNode(DerivedNode[Money]):
    """Computes Stamp Duty Land Tax from the property price."""

    def __init__(self, node_id: str, *, rightmove_price):
        super().__init__(node_id, Money, (rightmove_price,))

    def compute(self, price: Attempt[Money]) -> Attempt[Money]:
        if not price.succeeded:
            return Attempt.impossible("no price")
        price_val = price.value_or_none()
        if price_val is None:
            return Attempt.impossible("no price")
        from houses.stamp_duty import stamp_duty_land_tax

        return Attempt.succeeded(stamp_duty_land_tax(price_val))

    async def build_provenance(self):
        return Provenance(label="stamp_duty_formula")
