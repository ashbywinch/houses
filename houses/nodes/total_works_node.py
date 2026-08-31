from __future__ import annotations

from typing import override

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.derived_node import DerivedNode
from dag.node import Node


class TotalWorksNode(DerivedNode[Money]):
    """Total works estimate for this property.

    Sums the ``works_estimates`` dict. Gates on any non-child person
    with ``works_estimate_required=True`` who is missing from the dict.
    """

    @override
    @property
    def provenance_formula(self) -> Formula | None:
        if not self._attempt.succeeded or self._attempt.value_or_none() is None:
            return None
        lines = []
        wd = self._works_estimates_node.latest_attempt().value_or_none() or {}
        for name, val in wd.items():
            if val is None:
                continue
            amt = val.amount if isinstance(val, Money) else str(val)
            lines.append(FormulaLine(label=f"{name}’s renovation estimate", value=f"£{amt:,.2f}"))
        if not lines:
            lines.append(FormulaLine(label="Total Works", value=str(self._attempt.value)))
        return Formula(lines=lines, result=str(self._attempt.value))

    def __init__(self, node_id: str, *, persons_source, works_estimates_node):
        super().__init__(node_id, Money, (persons_source, works_estimates_node))
        self._persons_source: Node = persons_source
        self._works_estimates_node: Node = works_estimates_node

    @override
    def compute(
        self,
        persons: Attempt[list],
        works_ests: Attempt[dict],
    ) -> Attempt[Money]:
        self._assert_deps_succeeded(persons=persons, works_ests=works_ests)
        ps = persons.value_or_none() or []
        buyers = [p for p in ps if not getattr(p, "is_child", False)]
        wd = works_ests.value_or_none() or {}

        missing = [
            p
            for p in buyers
            if getattr(p, "works_estimate_required", False) and (p.name not in wd or wd[p.name] is None)
        ]
        if missing:
            names = ", ".join(p.name for p in missing)
            return Attempt.impossible(f"Works estimate required for: {names}")

        # Filter out None values (cleared estimates); sum as Money
        total = Money(amount="0", currency="GBP")
        for v in wd.values():
            if v is None:
                continue
            if isinstance(v, Money):
                total += v
            else:
                total += Money(str(v), "GBP")
        return Attempt.succeeded(total)
