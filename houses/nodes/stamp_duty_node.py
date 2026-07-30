from __future__ import annotations

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode


class StampDutyNode(DerivedNode[Money]):
    """Computes Stamp Duty Land Tax from the property price.

    Returns £0 when Status is "Current" (owner-occupied — no purchase).
    """

    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        price_att = self._price_node.latest_attempt()
        price_val = price_att.value_or_none()
        lines = [
            FormulaLine(label="Property Price", value=str(price_val) if price_val else "—"),
            FormulaLine(label="First-time buyer relief", value="N/A"),
        ]
        return Formula(lines=lines, result=str(self._attempt.value))

    def __init__(self, node_id: str, *, rightmove_price, status_node=None):
        self._price_node = rightmove_price
        self._status_node = status_node
        deps = [rightmove_price]
        if status_node is not None:
            deps.append(status_node)
        super().__init__(node_id, Money, tuple(deps))

    def _get_active_deps(self):
        if self._status_node is not None:
            return (self._price_node, self._status_node)
        return (self._price_node,)

    def compute(
        self,
        price: Attempt[Money],
        status: Attempt[str] | None = None,
    ) -> Attempt[Money]:
        self._assert_deps_succeeded(price=price, status=status)

        # Current properties pay no stamp duty
        is_current = (
            status is not None
            and status.value_or_none().strip().lower() == "current"
        )
        if is_current:
            return Attempt.succeeded(Money("0", "GBP"))

        from houses.stamp_duty import stamp_duty_land_tax

        return Attempt.succeeded(stamp_duty_land_tax(price.value_or_none()))
