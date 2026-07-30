from __future__ import annotations

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode
from houses.council_tax_info import CouncilTaxInfo


class TotalMonthlyHousingCostNode(DerivedNode[Money]):
    """Total monthly housing cost.

    = Mortgage + SinkingFund(monthly share) + LifeInsurance
      + Commute + CouncilTax - RentalIncome

    When Status is "Current", sinking fund and life insurance are excluded
    (the owner already lives there).
    """

    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        lines = []

        mortgage_att = self._mortgage_node.latest_attempt()
        mortgage_val = mortgage_att.value_or_none() if mortgage_att.succeeded else None
        if mortgage_val is not None:
            lines.append(FormulaLine(label="Mortgage", value=str(mortgage_val)))

        sinking_att = self._sinking_node.latest_attempt()
        sinking_val = sinking_att.value_or_none() if sinking_att.succeeded else None
        if sinking_val is not None:
            monthly_money = sinking_val / 12
            our_share = monthly_money * 2 / 3
            lines.append(FormulaLine(label="Sinking Fund (yearly)", value=str(sinking_val)))
            lines.append(FormulaLine(label="  ÷ 12 (monthly)", value=f"{monthly_money.amount:.2f} GBP"))
            lines.append(FormulaLine(label="  × ⅔ (our share)", value=f"{our_share.amount:.2f} GBP"))
            lines.append(FormulaLine(label="Sinking Fund (monthly)", value=f"{our_share.amount:.2f} GBP"))

        life_ins_att = self._life_insurance_node.latest_attempt()
        life_ins_val = life_ins_att.value_or_none() if life_ins_att.succeeded else None
        if life_ins_val is not None:
            lines.append(FormulaLine(label="Life Insurance", value=str(life_ins_val)))

        commute_att = self._commute_node.latest_attempt()
        commute_val = commute_att.value_or_none() if commute_att.succeeded else None
        if commute_val is not None:
            lines.append(FormulaLine(label="Commute", value=str(commute_val)))

        council_att = self._council_tax_node.latest_attempt()
        council_val = council_att.value_or_none() if council_att.succeeded else None
        if council_val is not None:
            lines.append(FormulaLine(label="Council Tax", value=str(council_val)))

        rent_att = self._rental_income_node.latest_attempt()
        rent_val = rent_att.value_or_none() if rent_att.succeeded else None
        if rent_val is not None and rent_val.amount > 0:
            lines.append(FormulaLine(label="Rental Income", value=f"-{rent_val}"))

        return Formula(lines=lines, result=str(self._attempt.value))

    def __init__(
        self,
        node_id: str,
        *,
        monthly_mortgage_node,
        yearly_sinking_fund_node,
        life_insurance_node,
        rental_income_node,
        status_node,
        financial_source,
        commute_breakdown_node,
        council_tax_node,
    ):
        super().__init__(
            node_id,
            Money,
            (
                monthly_mortgage_node,
                yearly_sinking_fund_node,
                life_insurance_node,
                rental_income_node,
                status_node,
                financial_source,
                commute_breakdown_node,
                council_tax_node,
            ),
        )
        self._mortgage_node = monthly_mortgage_node
        self._sinking_node = yearly_sinking_fund_node
        self._life_insurance_node = life_insurance_node
        self._rental_income_node = rental_income_node
        self._status_node = status_node
        self._commute_node = commute_breakdown_node
        self._council_tax_node = council_tax_node

    def compute(
        self,
        mortgage: Attempt[Money],
        sinking: Attempt[Money],
        life_insurance: Attempt[Money],
        rental_income: Attempt[Money],
        status: Attempt[str],
        financial: Attempt[dict],
        commute: Attempt[dict],
        council_tax: Attempt[CouncilTaxInfo],
    ) -> Attempt[Money]:
        self._assert_deps_succeeded(
            mortgage=mortgage,
            sinking=sinking,
            life_insurance=life_insurance,
            rental_income=rental_income,
            status=status,
            financial=financial,
            commute=commute,
            council_tax=council_tax,
        )

        is_current = (
            status.value_or_none().strip().lower() == "current"
        )

        total = Money("0", "GBP")

        total += mortgage.value_or_none()

        if not is_current:
            sv = sinking.value_or_none()
            total += sv / 12 * 2 / 3

            total += life_insurance.value_or_none()

        # Commute cost
        cb = commute.value_or_none() or {}
        yt = cb.get("yearly_total_gbp", "0")
        if isinstance(yt, Money):
            total += yt / 12
        else:
            total += Money(str(yt), "GBP") / 12

        # Council tax
        ct_val = council_tax.value_or_none()
        if ct_val is not None and ct_val.yearly_cost is not None:
            total += ct_val.yearly_cost / 12

        # Rental income (subtracted)
        ri = rental_income.value_or_none()
        if ri.amount > 0:
            total -= ri

        return Attempt.succeeded(total)
