from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode

_ZERO = Decimal("0")
_TWO = Decimal("2")
_TWELVE = Decimal("12")
_HUNDRED = Decimal("100")


class MonthlyMortgagePaymentNode(DerivedNode[Money]):
    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        price_att = self._price_node.latest_attempt()
        sd_att = self._stamp_duty_node.latest_attempt()
        fin_att = self._financial_source.latest_attempt()

        lines = []
        price_val = price_att.value_or_none()
        if price_val is not None:
            lines.append(FormulaLine(label="Price", value=str(price_val)))

        sd_val = sd_att.value_or_none() if sd_att.succeeded else None
        if sd_val is not None:
            lines.append(FormulaLine(label="Stamp Duty", value=str(sd_val)))

        # Equity from persons
        persons_att = self._persons_source.latest_attempt()
        equity_total = _ZERO
        if persons_att.succeeded:
            for p in persons_att.value_or_none() or []:
                eq = getattr(p, "deposit_equity", None)
                if eq is not None:
                    equity_total += eq.amount if hasattr(eq, "amount") else Decimal(str(eq))
        if equity_total > _ZERO:
            lines.append(FormulaLine(label="Total Equity", value=str(equity_total)))

        fin = fin_att.value_or_none() or {} if fin_att.succeeded else {}
        rate = Decimal(str(fin.get("mortgage_rate", "0.045")))
        term = int(fin.get("mortgage_term_years", 30))
        lines.append(FormulaLine(label="Interest Rate", value=f"{float(rate) * 100:.1f}%"))
        lines.append(FormulaLine(label="Term", value=f"{term} years"))

        return Formula(lines=lines, result=str(self._attempt.value))

    def __init__(self, node_id: str, *, rightmove_price, stamp_duty_node, persons_source, financial_source):
        super().__init__(node_id, Money, (rightmove_price, stamp_duty_node, persons_source, financial_source))
        self._price_node = rightmove_price
        self._stamp_duty_node = stamp_duty_node
        self._persons_source = persons_source
        self._financial_source = financial_source

    def compute(
        self, price: Attempt[Money], stamp_duty: Attempt[Money], persons: Attempt[list], financial: Attempt[dict]
    ) -> Attempt[Money]:
        if not price.succeeded or price.value_or_none() is None:
            return Attempt.succeeded(Money("0", "GBP"))
        price_val = price.value_or_none()
        p = price_val.amount
        fin = (financial.value_or_none() or {}) if financial.succeeded else {}
        if p == _ZERO:
            return Attempt.succeeded(Money("0", "GBP"))

        # Total deposit equity from all persons
        sd_val = stamp_duty.value_or_none().amount if stamp_duty.succeeded and stamp_duty.value_or_none() else _ZERO
        total_equity = _ZERO
        if persons.succeeded:
            for person in persons.value_or_none() or []:
                eq = person.deposit_equity
                if eq is not None:
                    total_equity += eq.amount if hasattr(eq, "amount") else Decimal(str(eq))

        # Mortgage principal = price + stamp_duty - total_equity
        principal = p + sd_val - total_equity
        if principal <= _ZERO:
            return Attempt.succeeded(Money("0", "GBP"))

        rate = Decimal(str(fin.get("mortgage_rate", "0.045")))
        term = int(fin.get("mortgage_term_years", 30))
        monthly_rate = rate / _TWELVE
        n_payments = term * 12
        if monthly_rate == _ZERO:
            monthly = principal / n_payments
        else:
            numerator = monthly_rate * (1 + monthly_rate) ** n_payments
            denominator = (1 + monthly_rate) ** n_payments - 1
            monthly = principal * numerator / denominator
        return Attempt.succeeded(Money(str(monthly.quantize(Decimal("0.01"))), "GBP"))
