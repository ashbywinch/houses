from __future__ import annotations

from dataclasses import dataclass

from money import Money


@dataclass(frozen=True)
class CouncilTaxInfo:
    """Council tax band, cost, and evidence source."""

    band: str = ""
    yearly_cost: Money | None = None
    evidence_url: str = ""
