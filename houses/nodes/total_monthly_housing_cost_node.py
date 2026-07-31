from __future__ import annotations

from money import Money

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.expression import Attr, Conditional, Div, Field, Literal, Ref


class TotalMonthlyHousingCostNode(DerivedNode[Money]):
    """Total monthly housing cost.

    = Mortgage + SinkingFund(monthly) + LifeInsurance + Commute + CouncilTax - RentalIncome
    When Status is "Current", sinking fund and life insurance are excluded.
    """

    def __init__(
        self,
        node_id: str,
        *,
        monthly_mortgage_node,
        yearly_sinking_fund_node,
        life_insurance_node,
        rental_income_node,
        status_node,
        commute_breakdown_node,
        council_tax_node,
    ):
        self._status_node = status_node
        self._sinking_node = yearly_sinking_fund_node
        self._life_insurance_node = life_insurance_node
        super().__init__(
            node_id,
            Money,
            (
                monthly_mortgage_node,
                rental_income_node,
                status_node,
                commute_breakdown_node,
                council_tax_node,
                yearly_sinking_fund_node,
                life_insurance_node,
            ),
        )

    @property
    def expression(self):
        return (
            Ref(self._deps[0])  # mortgage
            + Conditional(
                predicate=lambda: (
                    (self._status_node.latest_attempt().value_or_none() or "").strip().lower() != "current"
                ),
                if_true=(
                    Div(Ref(self._sinking_node), Literal(12)) * Literal(2) / Literal(3)
                    + Ref(self._life_insurance_node)
                ),
                if_false=Literal(Money("0", "GBP")),
            )
            + Conditional(
                predicate=lambda: bool(
                    self._deps[3].latest_attempt().value_or_none()
                    and isinstance(self._deps[3].latest_attempt().value_or_none(), dict)
                    and "yearly_total_gbp" in self._deps[3].latest_attempt().value_or_none()
                ),
                if_true=Div(Field(Ref(self._deps[3]), "yearly_total_gbp"), Literal(12)),
                if_false=Literal(Money("0", "GBP")),
            )
            + Conditional(
                predicate=lambda: bool(
                    self._deps[4].latest_attempt().succeeded
                    and self._deps[4].latest_attempt().value_or_none() is not None
                    and hasattr(self._deps[4].latest_attempt().value_or_none(), "yearly_cost")
                    and self._deps[4].latest_attempt().value_or_none().yearly_cost is not None
                ),
                if_true=Div(Attr(Ref(self._deps[4]), "yearly_cost"), Literal(12)),
                if_false=Literal(Money("0", "GBP")),
            )
            - Ref(self._deps[1])  # rental_income
        )

    def _get_active_deps(self):
        status_att = self._status_node.latest_attempt()
        is_current = status_att.succeeded and (status_att.value_or_none() or "").strip().lower() == "current"
        if is_current:
            return self._deps[:5]
        return self._deps

    def compute(
        self,
        mortgage: Attempt[Money],
        rental_income: Attempt[Money],
        status: Attempt[str],
        commute: Attempt[dict],
        council_tax: Attempt,
        sinking: Attempt[Money] | None = None,
        life_insurance: Attempt[Money] | None = None,
    ) -> Attempt[Money]:
        return self.expression.evaluate()
