from __future__ import annotations

from dataclasses import dataclass

from money import Money

from dag.measurement import Measurement


@dataclass(frozen=True)
class CouncilTaxInfo:
    """Council tax band, cost, and evidence source.

    ``yearly_cost`` is a Measurement: exact (stddev 0) when looked up,
    an estimate with a spread when the lookup failed and the node
    fell back to a Band D approximation.
    """

    band: str = ""
    yearly_cost: Measurement[Money] | None = None
    evidence_url: str = ""

    def to_provenance_value(self) -> dict:
        """JSON-safe projection for provenance display."""
        return {
            "band": self.band,
            "yearly_cost": str(self.yearly_cost.value) if self.yearly_cost is not None else None,
            "uncertainty": self.yearly_cost.stddev if self.yearly_cost is not None else 0.0,
            "evidence_url": self.evidence_url,
        }
