from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode

_ZERO = Decimal("0")
_TWELVE = Decimal("12")
_HUNDRED = Decimal("100")


class MonthlyMortgagePaymentNode(DerivedNode[Money]):
    """Monthly mortgage payment via PMT formula.

    Depends on mortgage_required_node (not persons_source directly).
    """

    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        mortgage_att = self._mortgage_required_node.latest_attempt()
        fin_att = self._financial_source.latest_attempt()

        lines = []
        mr_val = mortgage_att.value_or_none()
        if mr_val is not None:
            lines.append(
                FormulaLine(
                    label="Mortgage Required", value=str(mr_val)
                )
            )

        fin = fin_att.value_or_none() or {} if fin_att.succeeded else {}
        rate = Decimal(str(fin.get("mortgage_rate", "0.045")))
        term = int(fin.get("mortgage_term_years", 30))
        lines.append(
            FormulaLine(
                label="Interest Rate", value=f"{float(rate) * 100:.1f}%"
            )
        )
        lines.append(
            FormulaLine(label="Term", value=f"{term} years")
        )

        return Formula(
            lines=lines, result=str(self._attempt.value)
        )

    def __init__(
        self, node_id: str, *, mortgage_required_node, financial_source
    ):
        super().__init__(
            node_id,
            Money,
            (mortgage_required_node, financial_source),
        )
        self._mortgage_required_node = mortgage_required_node
        self._financial_source = financial_source

    def compute(
        self,
        mortgage_required: Attempt[Money],
        financial: Attempt[dict],
    ) -> Attempt[Money]:
        if mortgage_required.impossible:
            return Attempt.impossible(mortgage_required.error)

        principal_att = mortgage_required.value_or_none()
        if not mortgage_required.succeeded or principal_att is None:
            return Attempt.succeeded(Money("0", "GBP"))

        principal = principal_att.amount
        if principal == _ZERO:
            return Attempt.succeeded(Money("0", "GBP"))

        fin = (
            (financial.value_or_none() or {})
            if financial.succeeded
            else {}
        )
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
        return Attempt.succeeded(
            Money(str(monthly.quantize(Decimal("0.01"))), "GBP")
        )
