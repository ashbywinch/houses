from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode
from houses.model.domain import Person
from houses.model.domain import effective_selling_home as _selling_home

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
        if status_node is not None:
            deps.append(status_node)
        super().__init__(node_id, Money, tuple(deps))

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

        zero = Money("0", "GBP")
        ps = persons.value_or_none()
        total = _ZERO
        for p in ps:
            sale = getattr(p, "home_sale_price", zero)
            mortgage = getattr(p, "outstanding_mortgage", zero)
            cash = getattr(p, "cash_contribution", zero)
            sale_amt = sale.amount if isinstance(sale, Money) else Decimal(str(sale))
            mortgage_amt = mortgage.amount if isinstance(mortgage, Money) else Decimal(str(mortgage))
            cash_amt = cash.amount if isinstance(cash, Money) else Decimal(str(cash))
            # Home equity counts ONLY when the person is selling a home —
            # otherwise the deposit is cash alone (a person with no home
            # must not leak stale home fields into the deposit).  Legacy
            # dict entries (tolerated by the getattr reads above) fall
            # back to the cash path rather than crashing the node.
            equity = (
                max(_ZERO, sale_amt - mortgage_amt)
                if isinstance(p, Person) and _selling_home(p)
                else _ZERO
            )
            # Cash contributions excluded for Current (owner-occupied) properties
            if not is_current:
                equity += cash_amt
            total += equity
        return Attempt.succeeded(Money(str(total), "GBP"))
