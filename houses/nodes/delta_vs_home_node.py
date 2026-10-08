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

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, TypedDict, cast, override

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.nodes.current_home_node import MonthlyBaseline, MonthlySide, as_figure


class DeltaVsHomeWire(TypedDict):
    """The delta block as the wire states it: each side's figure, or null."""

    couple: MonthlySide | None
    others: MonthlySide | None


@dataclass(frozen=True)
class DeltaVsHomeProvenanceValue:
    """The delta block as it appears in the provenance tree.

    A value object, like BaselineProvenanceValue: the DAG's projector takes
    any non-dict value object's own ``to_provenance_value()`` and walks its
    result, which must be plain JSON. The wire block (DeltaVsHomeWire) is a
    separate, wire-format shape.
    """

    couple: str | None
    others: str | None

    def to_provenance_value(self) -> dict[str, str | None]:
        """The tree entry: plain JSON, one key per side."""
        return cast(dict[str, str | None], asdict(self))


def _side_text(figure: MonthlySide | None) -> str | None:
    """One side's delta as the tree's human figure (None when uncomputable)."""
    return f"{figure.value}/mo" if figure is not None else None


@dataclass(frozen=True)
class DeltaVsHomeValue:
    """The per-group delta block (wire shape owned at to_wire)."""

    couple: MonthlySide | None
    others: MonthlySide | None

    def to_wire(self) -> DeltaVsHomeWire:
        # The side shape is the shared {value, approx} contract (the frontend's
        # MonthlyDeltaSide); DagJSONEncoder projects each MonthlySide to it.
        return DeltaVsHomeWire(couple=self.couple, others=self.others)

    def to_provenance_value(self) -> DeltaVsHomeProvenanceValue:
        """The tree states the per-side deltas as human figures."""
        return DeltaVsHomeProvenanceValue(
            couple=_side_text(self.couple),
            others=_side_text(self.others),
        )


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
        sides: dict[str, MonthlySide | None] = {}
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
            sides[side] = MonthlySide(value=f"{delta:+.2f}", approx=approx)
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