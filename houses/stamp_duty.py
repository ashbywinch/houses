"""Stamp Duty Land Tax (SDLT) for England — standard non-first-time-buyer rates."""

from decimal import Decimal

from money import Money


def stamp_duty_land_tax(price: Money) -> Money:
    """Standard non-first-time-buyer SDLT for England.

    Args:
        price: Property purchase price as Money in GBP.

    Returns:
        SDLT amount as Money.
    """
    p = price.amount  # Decimal
    if p <= Decimal("250000"):
        result = Decimal("0")
    elif p <= Decimal("925000"):
        result = (p - Decimal("250000")) * Decimal("0.05")
    elif p <= Decimal("1500000"):
        result = (p - Decimal("925000")) * Decimal("0.10") + Decimal("33750")
    else:
        result = (p - Decimal("1500000")) * Decimal("0.12") + Decimal("91250")
    return Money(str(result), "GBP")
