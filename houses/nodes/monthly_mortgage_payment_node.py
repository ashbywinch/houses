from __future__ import annotations

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode


class MonthlyMortgagePaymentNode(DerivedNode[Money]):
    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        try:
            price_att = self._deps[0].latest_attempt()
            sd_att = self._deps[1].latest_attempt()
            fin_att = self._deps[3].latest_attempt()

            lines = []
            price_val = price_att.value_or_none()
            if price_val is not None:
                lines.append(FormulaLine(label="Price", value=str(price_val)))

            sd_val = sd_att.value_or_none() if sd_att.succeeded else None
            if sd_val is not None:
                lines.append(FormulaLine(label="Stamp Duty", value=str(sd_val)))

            # Equity from persons
            persons_att = self._deps[2].latest_attempt()
            equity_total = 0.0
            if persons_att.succeeded:
                for p in persons_att.value_or_none() or []:
                    eq = getattr(p, "deposit_equity", None)
                    if eq is not None:
                        equity_total += float(getattr(eq, "amount", eq))
            if equity_total > 0:
                lines.append(FormulaLine(label="Total Equity", value=str(equity_total)))

            fin = fin_att.value_or_none() or {} if fin_att.succeeded else {}
            rate = float(fin.get("mortgage_rate", 0.045))
            term = int(fin.get("mortgage_term_years", 30))
            lines.append(FormulaLine(label="Interest Rate", value=f"{rate * 100:.1f}%"))
            lines.append(FormulaLine(label="Term", value=f"{term} years"))

            return Formula(lines=lines, result=str(self._attempt.value))
        except Exception:
            return None

    def __init__(self, node_id: str, *, rightmove_price, stamp_duty_node, persons_source, financial_source):
        super().__init__(node_id, Money, (rightmove_price, stamp_duty_node, persons_source, financial_source))

    def compute(
        self, price: Attempt[Money], stamp_duty: Attempt[Money], persons: Attempt[list], financial: Attempt[dict]
    ) -> Attempt[Money]:
        if not price.succeeded or price.value_or_none() is None:
            return Attempt.succeeded(Money("0", "GBP"))
        price_val = price.value_or_none()
        p = float(price_val.amount)
        fin = (financial.value_or_none() or {}) if financial.succeeded else {}
        if p == 0:
            return Attempt.succeeded(Money("0", "GBP"))

        # Total equity from all non-child persons
        sd_val = (
            float(stamp_duty.value_or_none().amount) if stamp_duty.succeeded and stamp_duty.value_or_none() else 0.0
        )
        total_equity = 0.0
        if persons.succeeded:
            for person in persons.value_or_none() or []:
                eq = person.deposit_equity
                if eq is not None:
                    total_equity += float(eq.amount) if hasattr(eq, "amount") else float(eq)

        # Mortgage principal = price + stamp_duty - total_equity
        principal = p + sd_val - total_equity
        if principal <= 0:
            return Attempt.succeeded(Money("0", "GBP"))

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
        return Attempt.succeeded(Money(str(round(monthly, 2)), "GBP"))
