from __future__ import annotations

from dataclasses import dataclass

from money import Money


@dataclass(frozen=True)
class CouncilTaxInfo:
    """Council tax band, cost, and evidence source."""

    band: str = ""
    yearly_cost: Money | None = None
    evidence_url: str = ""

    def to_provenance_value(self) -> dict:
        """JSON-safe projection for provenance display."""
        return {
            "band": self.band,
            "yearly_cost": str(self.yearly_cost) if self.yearly_cost is not None else None,
            "evidence_url": self.evidence_url,
        }
