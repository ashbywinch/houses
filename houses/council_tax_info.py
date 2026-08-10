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

    def to_provenance_value(self) -> str:
        """Human summary for provenance display.

        The old projection returned a machine dict (band / "GBP …"
        yearly_cost / uncertainty / evidence_url) that the provenance
        tree dumped as-is. The evidence URL moves to the provenance
        ``url`` field (CouncilTaxNode), so it renders as a link.
        """
        if self.yearly_cost is None:
            return f"Band {self.band}" if self.band else "Council tax"
        amount = self.yearly_cost.value.amount
        return f"Band {self.band} · £{amount:,.2f}/yr"
