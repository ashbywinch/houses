from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode

_ZERO = Decimal("0")


class LifeInsuranceTotalNode(DerivedNode[Money]):
    """Total monthly life insurance across all persons."""

    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        lines = [
            FormulaLine(
                label="Life Insurance Total",
                value=str(self._attempt.value),
            ),
        ]
        return Formula(lines=lines, result=str(self._attempt.value))

    def __init__(self, node_id: str, *, persons_source):
        super().__init__(node_id, Money, (persons_source,))
        self._persons_source = persons_source

    def compute(self, persons: Attempt[list]) -> Attempt[Money]:
        self._assert_deps_succeeded(persons=persons)
        zero = Money("0", "GBP")
        ps = persons.value_or_none()
        total = _ZERO
        for p in ps:
            ins = getattr(p, "life_insurance_monthly", zero)
            amt = (
                ins.amount
                if isinstance(ins, Money)
                else Decimal(str(ins))
            )
            total += amt
        return Attempt.succeeded(Money(str(total), "GBP"))
