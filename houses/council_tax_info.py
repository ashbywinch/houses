from __future__ import annotations

from dataclasses import dataclass

from money import Money

from dag.measurement import Measurement


@dataclass(frozen=True)
class AnnexeDwelling:
    """A separate council-tax dwelling at the same address (annexe/flat).

    Detected when exactly one other VOA property at the postcode is the
    main property's address with a unit prefix — e.g. "FLAT 2, 2
    WILLOWMEAD GARDENS" contains "2 WILLOWMEAD GARDENS".  It is a
    separate dwelling with its own council tax bill.
    """

    address: str
    band: str
    yearly_cost: Measurement[Money] | None = None


@dataclass(frozen=True)
class CouncilTaxInfo:
    """Council tax band, cost, and evidence source.

    ``yearly_cost`` is a Measurement: exact (stddev 0) when looked up,
    an estimate with a spread when the lookup failed and the node
    fell back to a Band D approximation.  ``lookup_error`` carries the
    real failure reason on that fallback (e.g. which addresses were
    ambiguous) so the provenance can say WHY it's an estimate.
    """

    band: str = ""
    yearly_cost: Measurement[Money] | None = None
    evidence_url: str = ""
    lookup_error: str = ""
    annexe: AnnexeDwelling | None = None

    def to_provenance_value(self) -> str:
        """Human summary for provenance display.

        The old projection returned a machine dict (band / "GBP …"
        yearly_cost / uncertainty / evidence_url) that the provenance
        tree dumped as-is. The evidence URL moves to the provenance
        ``url`` field (CouncilTaxNode), so it renders as a link.
        """
        if self.yearly_cost is None:
            base = f"Band {self.band}" if self.band else "Council tax"
        else:
            amount = self.yearly_cost.value.amount
            base = f"Band {self.band} · £{amount:,.2f}/yr"
        if self.lookup_error:
            base += f" — {self.lookup_error}"
        if self.annexe is not None:
            base += f" · annexe {self.annexe.address} (Band {self.annexe.band})"
            if self.annexe.yearly_cost is not None:
                base += f" · £{self.annexe.yearly_cost.value.amount:,.2f}/yr"
        return base
