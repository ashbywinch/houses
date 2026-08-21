from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode

_ZERO = Decimal("0")


class EquityTotalNode(DerivedNode[Money]):
    """Total equity from all persons.

    For each person:
        max(0, home_sale_price - outstanding_mortgage) + cash_contribution

    When the property Status is "Current" (owner-occupied), cash_contributions
    are excluded — they apply only to new property purchases.
    """

    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        lines = [
            FormulaLine(label="Total Equity", value=str(self._attempt.value)),
        ]
        return Formula(lines=lines, result=str(self._attempt.value))

    def __init__(self, node_id: str, *, persons_source, status_node=None):
        self._persons_source = persons_source
        self._status_node = status_node
        deps = [persons_source]
        names = ["persons"]
        if status_node is not None:
            deps.append(status_node)
            names.append("status")
        super().__init__(node_id, Money, tuple(deps), dep_names=tuple(names))

    def _get_active_deps(self) -> tuple:
        if self._status_node is not None:
            return (self._persons_source, self._status_node)
        return (self._persons_source,)

    def compute(
        self,
        persons: Attempt[list],
        status: Attempt[str] | None = None,
    ) -> Attempt[Money]:
        self._assert_deps_succeeded(
            persons=persons,
            status=status,
        )

        is_current = status is not None and status.value_or_none().strip().lower() == "current"

        from houses.model.domain import home_equity_contributions

        ps = persons.value_or_none() or []
        contributions = home_equity_contributions(ps)
        total = _ZERO
        for p in ps:
            name = getattr(p, "name", None)
            if not name or name not in contributions:
                continue  # children / legacy entries never contribute
            share = contributions[name]
            if not is_current:
                cash = getattr(p, "cash_contribution", Money("0", "GBP"))
                share += cash.amount if isinstance(cash, Money) else Decimal(str(cash))
            total += share
        return Attempt.succeeded(Money(str(total), "GBP"))
