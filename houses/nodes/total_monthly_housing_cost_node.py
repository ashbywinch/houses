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
                    Div(Ref(self._sinking_node), Literal(12)) * Literal(2) / Literal(3) + Ref(self._life_insurance_node)
                ),
                if_false=Literal(Money("0", "GBP")),
            )
            + Div(Field(Ref(self._deps[3]), "yearly_total_gbp"), Literal(12))
            + Div(Attr(Ref(self._deps[4]), "yearly_cost"), Literal(12))
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
        total = Money("0", "GBP") + mortgage.value_or_none()

        is_current = (status.value_or_none() or "").strip().lower() == "current"
        if not is_current:
            sv = sinking.value_or_none()
            if sv:
                total += sv / 12 * 2 / 3
            total += life_insurance.value_or_none()

        # Commute cost (yearly -> monthly)
        cb = commute.value_or_none() or {}
        yt = cb.get("yearly_total_gbp", "0")
        if isinstance(yt, Money):
            total += yt / 12
        else:
            total += Money(str(yt), "GBP") / 12

        # Council tax (yearly -> monthly)
        ct = council_tax.value_or_none()
        if ct is not None and hasattr(ct, "yearly_cost") and ct.yearly_cost is not None:
            total += ct.yearly_cost / 12

        # Rental income (subtracted)
        ri = rental_income.value_or_none()
        if ri and ri.amount > 0:
            total -= ri

        return Attempt.succeeded(total)
