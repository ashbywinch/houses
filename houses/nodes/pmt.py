"""PMT — the monthly mortgage payment expression."""

from __future__ import annotations

from decimal import Decimal
from typing import override

from money import Money

from dag.attempt import Attempt, FormulaLine
from dag.expression import Expression


class PMT(Expression):
    """Monthly mortgage payment = P × r(1+r)^n / ((1+r)^n − 1)."""
    MONTHS_PER_YEAR = 12

    def __init__(
        self,
        principal: Expression,
        annual_rate: Expression,
        term_years: Expression,
        description: str = "Monthly mortgage payment calculated from the loan amount, interest rate, and term",
    ):
        self.principal: Expression = principal
        self.annual_rate: Expression = annual_rate
        self.term_years: Expression = term_years
        self.description: str = description

    @staticmethod
    def _evaluate_input(expr: Expression, missing: str) -> Attempt:
        """Evaluate one input into a succeeded attempt carrying its value."""
        result = expr.evaluate()
        if not result.succeeded:
            return Attempt.impossible(result.error or missing)
        if result.value is None:
            return Attempt.impossible(missing)
        return result


    @override
    def evaluate(self) -> Attempt:
        p_result = self._evaluate_input(self.principal, "principal missing")
        r_result = self._evaluate_input(self.annual_rate, "rate missing")
        t_result = self._evaluate_input(self.term_years, "term missing")
        for result in (p_result, r_result, t_result):
            if not result.succeeded:
                return result  # propagate the first missing/failed input

        p, r_raw, t_raw = p_result.value, r_result.value, t_result.value
        # The succeeded-loop above already returned on any failure — the
        # assert exists to narrow the value types for the checker.
        assert p is not None and r_raw is not None and t_raw is not None
        p = Decimal(str(p.amount)) if isinstance(p, Money) else Decimal(str(p))
        r = float(r_raw) / PMT.MONTHS_PER_YEAR
        n = int(t_raw) * PMT.MONTHS_PER_YEAR

        payment = p / Decimal(str(n)) if r == 0 else p * Decimal(str(r * (1 + r) ** n / ((1 + r) ** n - 1)))

        if isinstance(p_result.value, Money):
            payment = Money(str(round(payment, 2)), p_result.value.currency)

        return Attempt.succeeded(payment)

    @override
    def to_formula_lines(self) -> list[FormulaLine]:
        rate_attempt = self.annual_rate.evaluate()
        term_attempt = self.term_years.evaluate()
        lines = list(self.principal.to_formula_lines())
        for rl in self.annual_rate.to_formula_lines():
            r = rate_attempt.value_or_none()
            value = (
                f"{r} ÷ 12 = {float(r) / PMT.MONTHS_PER_YEAR:.4f}"
                if rate_attempt.succeeded and r is not None
                else rl.value
            )
            lines.append(FormulaLine(label=rl.label + " ÷ 12", value=value))
        for tl in self.term_years.to_formula_lines():
            t = term_attempt.value_or_none()
            value = (
                f"{t} × 12 = {int(t) * PMT.MONTHS_PER_YEAR}"
                if term_attempt.succeeded and t is not None
                else tl.value
            )
            lines.append(FormulaLine(label=tl.label + " × 12", value=value))
        return lines
