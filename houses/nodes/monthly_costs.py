from __future__ import annotations

from dag.attempt import Attempt, Provenance
from dag.computed_node import ComputedNode


class CommuteBreakdownNode(ComputedNode[dict]):
    """Sync node that computes daily/yearly commute costs.

    Deps: (commute_selector_nodes for simon/office, simon/bracknell, lorena/office,
           persons_source)
    """

    def __init__(self, node_id: str, *, simon_office, simon_bracknell, lorena_office,
                 persons_source):
        super().__init__(
            node_id,
            dict,
            (simon_office, simon_bracknell, lorena_office, persons_source),
        )

    def compute(self, simon_office: Attempt[dict],
                simon_bracknell: Attempt[dict],
                lorena_office: Attempt[dict],
                persons: Attempt[list]) -> Attempt[dict]:
        if not persons.is_succeeded:
            return self._impossible({"persons_source": persons})
        persons_val = persons.value_or_none() or []
        working_weeks = 46
        for p in persons_val:
            if p.get("name") == "Simon":
                for poi in p.get("places_of_interest", []):
                    if poi.get("label") == "Office":
                        working_weeks = poi.get("weeks_per_year", 46)
        def _get_daily_cost(commute_attempt: Attempt[dict]) -> float:
            if commute_attempt.is_succeeded:
                val = commute_attempt.value_or_none()
                if val and hasattr(val, 'daily_cost'):
                    return float(val.daily_cost.amount)
                if isinstance(val, dict):
                    dc = val.get("daily_cost", {})
                    if isinstance(dc, dict):
                        return float(dc.get("amount", 0))
            return 0.0
        simon_daily = _get_daily_cost(simon_office)
        bracknell_daily = _get_daily_cost(simon_bracknell)
        lorena_daily = _get_daily_cost(lorena_office)
        yearly_total = round(
            working_weeks * (bracknell_daily + simon_daily + lorena_daily * 2),
            2,
        )
        formula = (
            f"{working_weeks}wk x "
            f"(1xBracknell_daily + 1xSimon_daily + 2xLorena_daily)"
        )
        return Attempt.succeeded(
            {
                "simon_daily_gbp": simon_daily,
                "lorena_daily_gbp": lorena_daily,
                "bracknell_daily_gbp": bracknell_daily,
                "yearly_total_gbp": yearly_total,
                "formula_explanation": formula,
            },
            Provenance("formula:commute_breakdown",
                       description=formula),
        )


class MonthlyMortgagePaymentNode(ComputedNode[float]):
    """Sync node computing monthly mortgage payment via PMT formula.

    Deps: (rightmove_price, financial_source)
    """

    def __init__(self, node_id: str, *, rightmove_price, financial_source):
        super().__init__(
            node_id,
            float,
            (rightmove_price, financial_source),
        )

    def compute(self, price: Attempt[str],
                financial: Attempt[dict]) -> Attempt[float]:
        if not price.is_succeeded or not financial.is_succeeded:
            return Attempt.succeeded(0.0, Provenance("formula:no_data"))
        try:
            p = float(price.value_or_none())
        except (ValueError, TypeError):
            return Attempt.succeeded(0.0, Provenance("formula:bad_price"))
        fin = financial.value_or_none() or {}
        rate = fin.get("mortgage_rate", 0.045) / 12
        term = fin.get("mortgage_term_years", 30) * 12
        if rate <= 0 or term <= 0:
            return Attempt.succeeded(0.0, Provenance("formula:no_financials"))
        payment = p * (rate * (1 + rate) ** term) / ((1 + rate) ** term - 1)
        return Attempt.succeeded(
            round(payment, 2),
            Provenance("formula:pmt", description=f"mortgage on £{p} @ {rate*12*100:.1f}%"),
        )


class YearlySinkingFundNode(ComputedNode[float]):
    """Sync node: price × sinking_fund_rate.

    Deps: (rightmove_price, financial_source)
    """

    def __init__(self, node_id: str, *, rightmove_price, financial_source):
        super().__init__(
            node_id,
            float,
            (rightmove_price, financial_source),
        )

    def compute(self, price: Attempt[str],
                financial: Attempt[dict]) -> Attempt[float]:
        if not price.is_succeeded:
            return Attempt.succeeded(0.0, Provenance("formula:no_price"))
        try:
            p = float(price.value_or_none())
        except (ValueError, TypeError):
            return Attempt.succeeded(0.0, Provenance("formula:bad_price"))
        fin = financial.value_or_none() or {}
        rate = fin.get("sinking_fund_rate", 0.01)
        return Attempt.succeeded(
            round(p * rate, 2),
            Provenance("formula:sinking_fund",
                       description=f"{p} × {rate}"),
        )


class TotalMonthlyHousingCostNode(ComputedNode[float]):
    """Sync node: mortgage + sinking_fund + life_ins + commute + ct - rental.

    Deps: (monthly_mortgage_node, yearly_sinking_fund_node, financial_source,
           commute_breakdown_node, council_tax_node)
    """

    def __init__(self, node_id: str, *, monthly_mortgage_node,
                 yearly_sinking_fund_node, financial_source,
                 commute_breakdown_node, council_tax_node):
        super().__init__(
            node_id,
            float,
            (monthly_mortgage_node, yearly_sinking_fund_node, financial_source,
             commute_breakdown_node, council_tax_node),
        )

    def compute(self, mortgage: Attempt[float],
                sinking_fund: Attempt[float],
                financial: Attempt[dict],
                commute_breakdown: Attempt[dict],
                council_tax: Attempt[dict]) -> Attempt[float]:
        fin = financial.value_or_none() or {}
        monthly_mortgage = mortgage.value_or_none() or 0.0
        monthly_sinking = (sinking_fund.value_or_none() or 0.0) / 12 * 2 / 3
        life_ins = fin.get("life_insurance_monthly", 0) or 0
        rental = fin.get("rental_income_monthly", 0) or 0
        ct_yearly = 0.0
        if council_tax.is_succeeded:
            ct_val = council_tax.value_or_none() or {}
            ct_yearly = float(ct_val.get("yearly_cost", 0) or 0)
        monthly_ct = ct_yearly / 12 if ct_yearly > 0 else 0
        commute = commute_breakdown.value_or_none() or {}
        monthly_commute = 0.0
        yearly = commute.get("yearly_total_gbp")
        if yearly:
            monthly_commute = yearly / 12
        gross = monthly_mortgage + monthly_sinking + life_ins + monthly_commute + monthly_ct
        total = gross - rental
        return Attempt.succeeded(
            round(total, 2),
            Provenance("formula:total_monthly",
                       description="mortgage+sf+li+commute+ct-rental"),
        )
