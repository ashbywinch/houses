from __future__ import annotations

from decimal import Decimal

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode


class CommuteBreakdownNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *,
                 simon_office, simon_bracknell, lorena_office,
                 persons_source):
        super().__init__(node_id, dict,
                         (simon_office, simon_bracknell, lorena_office,
                          persons_source))

    def compute(self, simon_office: Attempt[dict],
                simon_bracknell: Attempt[dict],
                lorena_office: Attempt[dict],
                persons: Attempt[list]) -> Attempt[dict]:
        commute_total = 0.0
        if simon_office.succeeded:
            commute_total += 0.0  # placeholder
        if simon_bracknell.succeeded:
            commute_total += 0.0
        if lorena_office.succeeded:
            commute_total += 0.0
        return Attempt.succeeded({"yearly_total_gbp": commute_total})

    async def build_provenance(self):
        return Provenance(label="commute_breakdown")


class StampDutyNode(DerivedNode[float]):
    """Computes Stamp Duty Land Tax from the property price."""

    def __init__(self, node_id: str, *, rightmove_price):
        super().__init__(node_id, float, (rightmove_price,))

    def compute(self, price: Attempt[str]) -> Attempt[float]:
        if not price.succeeded:
            return Attempt.impossible("no price")
        p_str = price.value_or_none() or "0"
        try:
            p = float(p_str.replace(",", "").replace("£", ""))
        except (ValueError, TypeError):
            return Attempt.impossible(f"bad price: {p_str}")
        from houses.stamp_duty import stamp_duty_land_tax
        return Attempt.succeeded(stamp_duty_land_tax(p))

    async def build_provenance(self):
        return Provenance(label="stamp_duty_formula")


class MonthlyMortgagePaymentNode(DerivedNode[float]):
    def __init__(self, node_id: str, *,
                 rightmove_price, stamp_duty_node, persons_source, financial_source):
        super().__init__(node_id, float,
                         (rightmove_price, stamp_duty_node, persons_source, financial_source))

    def compute(self, price: Attempt[str],
                stamp_duty: Attempt[float],
                persons: Attempt[list],
                financial: Attempt[dict]) -> Attempt[float]:
        if not price.succeeded:
            return Attempt.succeeded(0.0)
        p_str = price.value_or_none() or "0"
        fin = (financial.value_or_none() or {}) if financial.succeeded else {}
        try:
            p = float(p_str.replace(",", "").replace("£", ""))
        except (ValueError, TypeError):
            return Attempt.impossible(f"bad price: {p_str}")
        if p == 0:
            return Attempt.succeeded(0.0)

        # Total equity from all non-child persons
        sd_val = stamp_duty.value_or_none() if stamp_duty.succeeded else 0.0
        total_equity = 0.0
        if persons.succeeded:
            for person in (persons.value_or_none() or []):
                if not person.is_child:
                    eq = person.deposit_equity
                    if eq is not None:
                        total_equity += float(eq.amount) if hasattr(eq, 'amount') else float(eq)

        # Mortgage principal = price + stamp_duty - total_equity
        principal = p + sd_val - total_equity
        if principal <= 0:
            return Attempt.succeeded(0.0)

        rate = float(fin.get("mortgage_rate", 0.045))
        term = int(fin.get("mortgage_term_years", 30))
        monthly_rate = rate / 12
        n_payments = term * 12
        if monthly_rate == 0:
            monthly = principal / n_payments
        else:
            numerator = monthly_rate * (1 + monthly_rate) ** n_payments
            denominator = (1 + monthly_rate) ** n_payments - 1
            monthly = principal * numerator / denominator
        return Attempt.succeeded(round(monthly, 2))

    async def build_provenance(self):
        return Provenance(label="mortgage_formula")


class YearlySinkingFundNode(DerivedNode[float]):
    def __init__(self, node_id: str, *,
                 rightmove_price, financial_source):
        super().__init__(node_id, float, (rightmove_price, financial_source))

    def compute(self, price: Attempt[str],
                financial: Attempt[dict]) -> Attempt[float]:
        if not price.succeeded and not financial.succeeded:
            return Attempt.succeeded(0.0)
        p_str = (price.value_or_none() or "0") if price.succeeded else "0"
        fin = (financial.value_or_none() or {}) if financial.succeeded else {}
        try:
            p = Decimal(p_str.replace(",", "").replace("£", ""))
        except Exception:
            return Attempt.impossible(f"bad price: {p_str}")
        if p == 0:
            return Attempt.succeeded(0.0)
        rate = float(fin.get("sinking_fund_rate", 0.01))
        return Attempt.succeeded(round(float(p) * rate, 2))

    async def build_provenance(self):
        return Provenance(label="sinking_fund_formula")


class TotalMonthlyHousingCostNode(DerivedNode[float]):
    def __init__(self, node_id: str, *,
                 monthly_mortgage_node, yearly_sinking_fund_node,
                 financial_source, commute_breakdown_node,
                 council_tax_node):
        super().__init__(node_id, float,
                         (monthly_mortgage_node, yearly_sinking_fund_node,
                          financial_source, commute_breakdown_node,
                          council_tax_node))

    def compute(self, mortgage: Attempt[float],
                sinking: Attempt[float],
                financial: Attempt[dict],
                commute: Attempt[dict],
                council_tax: Attempt[dict]) -> Attempt[float]:
        total = 0.0
        if mortgage.succeeded:
            total += mortgage.value_or_none() or 0.0
        if sinking.succeeded:
            total += (sinking.value_or_none() or 0.0) / 12 * 2 / 3
        if commute.succeeded:
            cb = commute.value_or_none() or {}
            total += cb.get("yearly_total_gbp", 0.0) / 46
        if council_tax.succeeded:
            ct_val = council_tax.value_or_none() or {}
            total += ct_val.get("cost", 0.0) / 12
        return Attempt.succeeded(round(total, 2))
    async def build_provenance(self):
        return Provenance(label="total_monthly_formula")
