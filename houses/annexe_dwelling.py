"""A separate council-tax dwelling at the same address (annexe/flat)."""

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
