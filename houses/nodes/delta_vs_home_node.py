"""The monthly "vs your home" delta as a DAG node.

Each property's delta is a derived value: its own ``group_monthly_cost``
minus THE current home's (``CurrentHomeNode``). Living in the DAG means
the subtraction is recalculated when EITHER side changes, and its
provenance is the DAG's own record — the two group figures and the
subtraction. The wire serializes the value; the on-demand provenance
endpoint serves the tree.

Reads never need to be live (docs/dag-library.md, Design Rules): a read
serves the persisted value; correctness is restored by the DAG's
recalculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, override

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.nodes.current_home_node import MonthlyBaseline, as_figure


@dataclass(frozen=True)
class DeltaFigure:
    """One group's delta: a signed 2-dp amount and whether either side's
    figure is an estimate (Part A)."""

    value: str
    approx: bool

    # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
    def to_dict(self) -> dict:
        return {"value": self.value, "approx": self.approx}


@dataclass(frozen=True)
class DeltaVsHomeValue:
    """The per-group delta block (wire shape owned at to_wire)."""

    couple: DeltaFigure | None
    others: DeltaFigure | None

    def to_wire(self) -> dict:
        # lucidlint: ignore record-shape to_wire construction IS the serialization boundary (coding-standards.md)
        return {
            "couple": self.couple.to_dict() if self.couple is not None else None,
            "others": self.others.to_dict() if self.others is not None else None,
        }

    def to_provenance_value(self) -> dict:
        """The tree states the per-side deltas as human figures."""
        return {
            side: (f"{getattr(self, side).value}/mo" if getattr(self, side) is not None else None)
            for side in ("couple", "others")
        }


def _figure_number(figure: Any) -> Decimal | None:
    as_fig = as_figure(figure)
    if as_fig is None or as_fig.value is None:
        return None
    try:
        return Decimal(str(as_fig.value))
    except Exception:
        return None


class DeltaVsHomeNode(DerivedNode):
    """Candidate's group monthly cost minus the current home's.

    None value: no current home, OR this property IS the current home
    (the UI shows no vs-row for the baseline itself).
    """

    def __init__(self, node_id: str, *, group_node: Node, current_home: Node) -> None:
        super().__init__(
            node_id,
            DeltaVsHomeValue | None,
            (group_node, current_home),
            dep_names=("group", "current_home"),
        )

    @override
    def compute(
        self,
        group: Attempt[dict],
        current_home: Attempt[MonthlyBaseline | None],
    ) -> Attempt[DeltaVsHomeValue | None]:
        own = group.value_or_none() if group.succeeded else None
        baseline = current_home.value_or_none() if current_home.succeeded else None
        if not isinstance(own, dict) or baseline is None:
            return Attempt.succeeded(None)
        if baseline.rid == self._id.split("/")[0]:
            return Attempt.succeeded(None)

        base_value = baseline.group_value
        sides: dict[str, DeltaFigure | None] = {}
        for side in ("couple", "others"):
            own_num = _figure_number(own.get(side))
            base_num = _figure_number(base_value.get(side))
            if own_num is None or base_num is None:
                sides[side] = None
                continue
            own_fig = as_figure(own.get(side))
            base_fig = as_figure(base_value.get(side))
            approx = bool(own_fig and own_fig.stddev) or bool(base_fig and base_fig.stddev)
            delta = own_num - base_num
            sides[side] = DeltaFigure(value=f"{delta:+.2f}", approx=approx)
        return Attempt.succeeded(
            DeltaVsHomeValue(couple=sides["couple"], others=sides["others"])
        )

    @staticmethod
    @override
    def provenance_display_value(att: Any) -> Any:
        """Parent trees state the delta block as the two per-side figures —
        never the raw dict."""
        v = att.value_or_none() if att is not None else None
        if not isinstance(v, DeltaVsHomeValue):
            return "No comparison" if v is None else str(v)
        return " · ".join(
            f"{side} {f.value}/mo"
            for side in ("couple", "others")
            if (f := getattr(v, side)) is not None
        )