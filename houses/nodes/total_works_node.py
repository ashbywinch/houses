from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode


class TotalWorksNode(DerivedNode[Money]):
    """Total works estimate for this property.

    Sums the ``works_estimates`` dict. Gates on any non-child person
    with ``works_estimate_required=True`` who is missing from the dict.
    """

    propagate_impossible = True

    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        lines = [
            FormulaLine(label="Total Works", value=str(self._attempt.value)),
        ]
        return Formula(lines=lines, result=str(self._attempt.value))

    def __init__(self, node_id: str, *, persons_source, works_estimates_node):
        super().__init__(node_id, Money, (persons_source, works_estimates_node))
        self._persons_source = persons_source
        self._works_estimates_node = works_estimates_node

    def compute(
        self,
        persons: Attempt[list],
        works_ests: Attempt[dict],
    ) -> Attempt[Money]:
        ps = persons.value_or_none() or []
        buyers = [p for p in ps if not getattr(p, "is_child", False)]
        wd = (
            works_ests.value_or_none()
            if works_ests.value_or_none() is not None
            else {}
        )

        missing = [
            p
            for p in buyers
            if getattr(p, "works_estimate_required", False)
            and (p.name not in wd or wd[p.name] is None)
        ]
        if missing:
            names = ", ".join(p.name for p in missing)
            return Attempt.impossible(
                f"Works estimate required for: {names}"
            )

        # Filter out None values (cleared estimates)
        total = sum(
            Decimal(str(v)) for v in wd.values() if v is not None
        )
        return Attempt.succeeded(Money(str(total), "GBP"))
