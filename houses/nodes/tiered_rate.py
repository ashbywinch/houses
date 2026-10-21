"""TieredRate — a marginal tax/rate calculation across bands."""

from __future__ import annotations

from decimal import Decimal
from typing import override

from money import Money

from dag.attempt import Attempt, FormulaLine
from dag.expression import Expression, Literal, Ref
from houses.nodes.tax_tier import TaxTier

type _TierTax = tuple[Decimal, Decimal]  # (tax in this tier, cumulative tax incl. it)


class TieredRate(Expression):
    """Marginal tax/rate calculation across multiple bands.

    Each tier is a ``TaxTier`` record (``rate_from`` inclusive,
    ``rate_to`` exclusive, ``rate``) where ``rate_to`` can be ``None`` for
    the final open-ended tier. The expression finds which tier the value
    falls in and computes:

        tax = (value - tier_start) * rate + tax_at_tier_start

    ``tax_at_tier_start`` is automatically computed from previous tiers so
    you don't need to specify bases manually.

    Example — stamp duty:

        TieredRate(self._price_node, tiers=[
            TaxTier(0, 250000, 0),
            TaxTier(250000, 925000, Decimal("0.05")),
            TaxTier(925000, 1500000, Decimal("0.10")),
            TaxTier(1500000, None, Decimal("0.12")),
        ])
    """
    PERCENT = 100

    def __init__(
        self,
        value,
        tiers: list[TaxTier],
        description: str = "",
    ):
        self.value: Expression = (
            value
            if isinstance(value, Expression)
            else Ref(value) if hasattr(value, "latest_attempt")
            else Literal(value)
        )
        self.tiers: list[TaxTier] = tiers
        self.description: str = description

    def _tax_at(self, price: Decimal, tier_idx: int) -> _TierTax:
        """Compute tax for a value in the given tier.

        Returns (tax_at_this_tier, cumulative_tax_including_this_tier).
        """
        tier = self.tiers[tier_idx]
        effective = (
            Decimal(str(tier.rate_to)) if tier.rate_to is not None and price > Decimal(str(tier.rate_to)) else price
        )
        taxable = effective - Decimal(str(tier.rate_from))
        tier_tax = taxable * Decimal(str(tier.rate))

        # Tax from all previous tiers at their maximum
        prev_tax = Decimal("0")
        for i in range(tier_idx):
            prev = self.tiers[i]
            pwidth = (
                Decimal(str(prev.rate_to)) - Decimal(str(prev.rate_from))
                if prev.rate_to is not None
                else Decimal("0")
            )
            prev_tax += pwidth * Decimal(str(prev.rate))

        return tier_tax, tier_tax + prev_tax

    @override
    def evaluate(self) -> Attempt:
        val_result = self.value.evaluate()
        if not val_result.succeeded:
            return val_result
        raw = val_result.value
        if raw is None:
            return Attempt.impossible("value missing")
        price = Decimal(str(raw.amount)) if hasattr(raw, "amount") else Decimal(str(raw))

        for i, tier in enumerate(self.tiers):
            lo_d = Decimal(str(tier.rate_from))
            hi_d = Decimal(str(tier.rate_to)) if tier.rate_to is not None else None

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

    @override
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

        for i, tier in enumerate(self.tiers):
            lo_d = Decimal(str(tier.rate_from))
            hi_d = Decimal(str(tier.rate_to)) if tier.rate_to is not None else None

            if price_d < lo_d:
                continue
            if hi_d is not None and price_d > hi_d:
                continue

            if lo_d == 0 and tier.rate == 0:
                lines.append(FormulaLine(label=f"First £{hi_d:,.0f} at 0%", value="£0.00"))
            else:
                prev_total = Decimal("0")
                for j in range(i):
                    prev = self.tiers[j]
                    pwidth = (
                        Decimal(str(prev.rate_to)) - Decimal(str(prev.rate_from))
                        if prev.rate_to is not None
                        else Decimal("0")
                    )
                    prev_total += pwidth * Decimal(str(prev.rate))
                    if prev.rate > 0:
                        lines.append(
                            FormulaLine(
                                label=(
                                    f"  £{prev.rate_from:,.0f} to £{prev.rate_to:,.0f}"
                                    f" at {float(prev.rate) * TieredRate.PERCENT:.0f}%"
                                ),
                                value=self._format_value(Money(str(prev_total), "GBP")),
                            )
                        )

                taxable = price_d - lo_d
                tier_tax = taxable * Decimal(str(tier.rate))
                pct = float(tier.rate) * TieredRate.PERCENT
                lines.append(
                    FormulaLine(
                        label=f"£{lo_d:,.0f} to £{price_d:,.0f} at {pct:.0f}%",
                        value=self._format_value(Money(str(tier_tax + prev_total), "GBP")),
                    )
                )
            break

        return lines
