"""Houses-domain expressions built on the generic dag.expression primitives.

These carry UK-specific financial logic (stamp duty, mortgage payment,
marginal rate bands) and therefore live in houses — the dag library stays
project-agnostic (enforced by tests/unit/dag/test_architecture.py).
"""

from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt, FormulaLine
from dag.expression import Expression, Literal


class PMT(Expression):
    """Monthly mortgage payment = P × r(1+r)^n / ((1+r)^n − 1)."""

    def __init__(
        self,
        principal: Expression,
        annual_rate: Expression,
        term_years: Expression,
        description: str = "Monthly mortgage payment calculated from the loan amount, interest rate, and term",
    ):
        self.principal = principal
        self.annual_rate = annual_rate
        self.term_years = term_years
        self.description = description

    def evaluate(self) -> Attempt:
        p_result = self.principal.evaluate()
        if not p_result.succeeded:
            return Attempt.impossible(p_result.error or "principal missing")
        r_result = self.annual_rate.evaluate()
        if not r_result.succeeded:
            return Attempt.impossible(r_result.error or "rate missing")
        t_result = self.term_years.evaluate()
        if not t_result.succeeded:
            return Attempt.impossible(t_result.error or "term missing")

        p = p_result.value
        if p is None:
            return Attempt.impossible("principal missing")
        p = Decimal(str(p.amount)) if isinstance(p, Money) else Decimal(str(p))
        r_raw = r_result.value
        if r_raw is None:
            return Attempt.impossible("rate missing")
        r = float(r_raw) / 12
        term = t_result.value
        if term is None:
            return Attempt.impossible("term missing")
        n = int(term) * 12

        payment = p / Decimal(str(n)) if r == 0 else p * Decimal(str(r * (1 + r) ** n / ((1 + r) ** n - 1)))

        if isinstance(p_result.value, Money):
            payment = Money(str(round(payment, 2)), p_result.value.currency)

        return Attempt.succeeded(payment)

    def to_formula_lines(self) -> list[FormulaLine]:
        lines: list[FormulaLine] = []
        lines.extend(self.principal.to_formula_lines())
        rate_lines = self.annual_rate.to_formula_lines()
        for rl in rate_lines:
            lines.append(FormulaLine(label=rl.label + " ÷ 12", value=rl.value))
        term_lines = self.term_years.to_formula_lines()
        for tl in term_lines:
            lines.append(FormulaLine(label=tl.label + " × 12", value=tl.value))
        return lines

class StampDutyFn(Expression):
    """Calculate UK Stamp Duty Land Tax from a property price."""

    def __init__(
        self,
        price: Expression,
        description: str = "UK Stamp Duty Land Tax — a one-off tax paid when buying a property",
    ):
        self.price = price
        self.description = description

    def evaluate(self) -> Attempt:
        price_result = self.price.evaluate()
        if not price_result.succeeded:
            return price_result
        price = price_result.value_or_none()
        if price is None:
            return Attempt.impossible("No price available for stamp duty calculation")
        from houses.stamp_duty import stamp_duty_land_tax

        try:
            return Attempt.succeeded(stamp_duty_land_tax(price))
        except Exception as e:
            return Attempt.impossible(f"Stamp duty calculation failed: {e}")

    def to_formula_lines(self) -> list[FormulaLine]:
        return self.price.to_formula_lines()

class TieredRate(Expression):
    """Marginal tax/rate calculation across multiple bands.

    Each tier is ``(from_inclusive, to_exclusive, rate)`` where ``to_exclusive``
    can be ``None`` for the final open-ended tier. The expression finds which
    tier the value falls in and computes:

        tax = (value - tier_start) * rate + tax_at_tier_start

    ``tax_at_tier_start`` is automatically computed from previous tiers so
    you don't need to specify bases manually.

    Example — stamp duty:

        TieredRate(self._price_node, tiers=[
            (0, 250000, 0),
            (250000, 925000, Decimal("0.05")),
            (925000, 1500000, Decimal("0.10")),
            (1500000, None, Decimal("0.12")),
        ])
    """

    def __init__(
        self,
        value,
        tiers: list[tuple],
        description: str = "",
    ):
        if isinstance(value, Expression):
            self.value = value
        elif hasattr(value, "latest_attempt"):
            from dag.expression import Ref

            self.value = Ref(value)
        else:
            self.value = Literal(value)
        self.tiers = tiers
        self.description = description

    def _tax_at(self, price: Decimal, tier_idx: int) -> tuple[Decimal, Decimal]:
        """Compute tax for a value in the given tier.

        Returns (tax_at_this_tier, cumulative_tax_including_this_tier).
        """
        lo, hi, rate = self.tiers[tier_idx]
        effective = Decimal(str(hi)) if hi is not None and price > Decimal(str(hi)) else price
        taxable = effective - Decimal(str(lo))
        tier_tax = taxable * Decimal(str(rate))

        # Tax from all previous tiers at their maximum
        prev_tax = Decimal("0")
        for i in range(tier_idx):
            plo, phi, prate = self.tiers[i]
            pwidth = Decimal(str(phi)) - Decimal(str(plo)) if phi is not None else Decimal("0")
            prev_tax += pwidth * Decimal(str(prate))

        return tier_tax, tier_tax + prev_tax

    def evaluate(self) -> Attempt:
        val_result = self.value.evaluate()
        if not val_result.succeeded:
            return val_result
        raw = val_result.value
        if raw is None:
            return Attempt.impossible("value missing")
        price = Decimal(str(raw.amount)) if hasattr(raw, "amount") else Decimal(str(raw))

        for i, (lo, hi, _rate) in enumerate(self.tiers):
            lo_d = Decimal(str(lo))
            hi_d = Decimal(str(hi)) if hi is not None else None

            if price < lo_d:
                continue
            if hi_d is not None and price > hi_d:
                continue

            tier_tax, total_tax = self._tax_at(price, i)
            result = total_tax
            break
        else:
            return Attempt.impossible(f"Price {price} does not fall in any tier")

        if hasattr(raw, "amount"):
            result_money = Money(str(result), raw.currency)
            return Attempt.succeeded(result_money)
        return Attempt.succeeded(result)  # raw is not None here (guarded above)

    def to_formula_lines(self) -> list[FormulaLine]:
        val_result = self.value.evaluate()
        if not val_result.succeeded:
            return [FormulaLine(label="Rate calculation", value="failed")]

        raw = val_result.value
        if raw is None:
            return [FormulaLine(label="Rate calculation", value="failed")]
        price_d = Decimal(str(raw.amount)) if hasattr(raw, "amount") else Decimal(str(raw))

        lines: list[FormulaLine] = []
        lines.append(FormulaLine(label="Property price", value=self._format_value(raw)))

        for i, (lo, hi, rate) in enumerate(self.tiers):
            lo_d = Decimal(str(lo))
            hi_d = Decimal(str(hi)) if hi is not None else None

            if price_d < lo_d:
                continue
            if hi_d is not None and price_d > hi_d:
                continue

            if lo_d == 0 and rate == 0:
                lines.append(FormulaLine(label=f"First £{hi_d:,.0f} at 0%", value="£0.00"))
            else:
                prev_total = Decimal("0")
                for j in range(i):
                    plo, phi, prate = self.tiers[j]
                    pwidth = Decimal(str(phi)) - Decimal(str(plo)) if phi is not None else Decimal("0")
                    prev_total += pwidth * Decimal(str(prate))
                    if prate > 0:
                        lines.append(
                            FormulaLine(
                                label=f"  £{plo:,.0f} to £{phi:,.0f} at {float(prate) * 100:.0f}%",
                                value=self._format_value(Money(str(prev_total), "GBP")),
                            )
                        )

                taxable = price_d - lo_d
                tier_tax = taxable * Decimal(str(rate))
                pct = float(rate) * 100
                lines.append(
                    FormulaLine(
                        label=f"£{lo_d:,.0f} to £{price_d:,.0f} at {pct:.0f}%",
                        value=self._format_value(Money(str(tier_tax + prev_total), "GBP")),
                    )
                )
            break

        return lines
