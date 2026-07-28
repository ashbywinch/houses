from __future__ import annotations

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode


class StampDutyNode(DerivedNode[Money]):
    """Computes Stamp Duty Land Tax from the property price."""

    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        try:
            price_att = self._deps[0].latest_attempt()
            price_val = price_att.value_or_none()
            lines = [
                FormulaLine(label="Property Price", value=str(price_val) if price_val else "—"),
                FormulaLine(label="First-time buyer relief", value="N/A"),
            ]
            return Formula(lines=lines, result=str(self._attempt.value))
        except Exception:
            return None

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
