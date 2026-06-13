"""Stamp Duty Land Tax (SDLT) for England — standard non-first-time-buyer rates."""


def stamp_duty_land_tax(price: float) -> float:
    """Standard non-first-time-buyer SDLT for England.

    Args:
        price: Property purchase price in GBP.

    Returns:
        SDLT amount in GBP.
    """
    if price <= 250000:
        return 0.0
    if price <= 925000:
        return (price - 250000) * 0.05
    if price <= 1500000:
        return (price - 925000) * 0.10 + 33750.0
    return (price - 1500000) * 0.12 + 91250.0
