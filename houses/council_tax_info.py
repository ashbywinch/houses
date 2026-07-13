from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CouncilTaxInfo:
    """Council tax band, cost, and evidence source."""

    band: str = ""
    yearly_cost: float | None = None
    evidence_url: str = ""
