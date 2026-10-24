"""TaxTier — one marginal band of a tiered-rate calculation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TaxTier:
    """One marginal band: ``rate_from`` inclusive, ``rate_to`` exclusive
    (``None`` for the final open-ended tier), ``rate`` the marginal rate."""

    rate_from: int | Decimal
    rate_to: int | Decimal | None
    rate: int | Decimal
