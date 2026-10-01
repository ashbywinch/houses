"""The current home as a DAG node.

"Current home" is a registry-global fact: exactly one property whose
``comment_status`` is "current". The monthly "vs your home" deltas need
it as a dependency, so it lives IN the DAG — a derived node whose value
is the baseline descriptor, re-derived when a status write fans out,
never re-scanned from the registry on a read.

Reads never need to be live: a read serves the node's persisted value;
the DAG recalculates it when a status (or the baseline property's cost
or address) changes (docs/dag-library.md, Design Rules → "Never live on
read").
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, override

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node

CURRENT_STATUS = "current"


def _figure_text(raw: Any) -> str | None:
    figure = _as_figure(raw)
    if figure is None or figure.value is None:
        return None
    return _money_text(figure.value)


def _money_text(value: str) -> str:
    """A figure's amount as display text (or the raw string if it is not
    a clean number — never swallow). Monetary values stay in Decimal."""
    try:
        amount = Decimal(value)
        return f"£{amount:,.2f}/mo"
    except (TypeError, ValueError, ArithmeticError):
        return value


@dataclass(frozen=True)
class _RawFigure:
    """A group figure ingested from the group value dict.

    ``value`` is the string amount, ``stddev`` its uncertainty (Part A:
    exact when 0). Kept at the ingestion edge; ``to_wire`` projects it.
    """

    value: str | None
    stddev: float

    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return {"value": self.value, "stddev": self.stddev}


def _as_figure(raw: object) -> _RawFigure | None:
    if isinstance(raw, dict):
        value = raw.get("value")
        if value is not None:
            try:
                return _RawFigure(value=str(value), stddev=float(raw.get("stddev") or 0))
            except (TypeError, ValueError):
                return None
    return None


def _figure_or_empty(raw: object) -> _RawFigure:
    """The ingested figure — an empty one when *raw* is absent (mirrors
    the historical ``or {}``: a missing couple figure serializes as
    "None")."""
    figure = _as_figure(raw)
    return figure if figure is not None else _RawFigure(value=None, stddev=0.0)


@dataclass(frozen=True)
class MonthlyBaseline:
    """The resolved current home: identity plus its raw group figures.

    ``group_value`` is the group_monthly_cost attempt value dict (couple
    and others, each ``{value, stddev}``) — kept raw so delta computation
    can read the stddevs; ``to_wire`` projects the contract shape.
    """

    rid: str
    address: str
    group_value: dict
    others_rent_paid: float

    def to_provenance_value(self) -> dict:
        """The tree states the baseline as identity + human figures."""
        return {
            "rid": self.rid,
            "address": self.address,
            "couple": _figure_text(self.group_value.get("couple")),
            "others": _figure_text(self.group_value.get("others")),
        }

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_wire(self) -> dict:
        # The contract shape is {value, approx} (see the frontend's
        # MonthlyDeltaSide): the stddev feeds the approx flag; the raw
        # stddev itself is the GROUP's wire, not the baseline's.
        couple = _figure_or_empty(self.group_value.get("couple"))
        others = _as_figure(self.group_value.get("others"))
        # lucidlint: ignore record-shape to_wire construction IS the serialization boundary (coding-standards.md)
        return {
            "rid": self.rid,
            "address": self.address,
            "couple": {"value": couple.value, "approx": bool(couple.stddev)},
            "others": {"value": others.value, "approx": bool(others.stddev)} if others is not None else None,
            "others_rent_paid": self.others_rent_paid,
        }


class CurrentHomeNode(DerivedNode):
    """THE current home: exactly one current-status property with a
    computable couple figure, else None.

    Deps: every registered property's status node (re-wired as properties
    register — a status write signals this node and fans the re-derivation
    out to every delta's consumer). The active set additionally includes
    the winning property's ``group_monthly_cost`` and ``best_address``
    nodes, so a re-price or an address edit of the baseline re-derives the
    descriptor through the same edges.
    """

    def __init__(self, node_id: str = "settings/current_home"):
        # dep_names=None: the dep set grows with registrations (set_deps);
        # compute receives attempts positionally in active-dep order.
        super().__init__(node_id, MonthlyBaseline | None, ())
        self._registry: Any = None

    # -- dep wiring ------------------------------------------
    def add_status(self, status_node: Node, registry: Any) -> None:
        """Register one property's status node (called as properties are
        registered); re-wires the signal edges so a status write fans out."""
        self._registry = registry
        self.set_deps((*self._deps, status_node))

    def _current_property(self) -> Any:
        if self._registry is None:
            return None
        statuses = {str(n._id).split("/")[0]: n for n in self._deps}
        for rid, node in statuses.items():
            att = node.latest_attempt()
            if att is None or not att.succeeded:
                continue
            if (att.value_or_none() or "").strip().lower() == CURRENT_STATUS:
                prop = self._registry.get(rid)
                return prop if prop is not None else None
        return None

    @override
    def _get_active_deps(self) -> tuple:
        prop = self._current_property()
        if prop is not None:
            extra = tuple(
                n for n in (getattr(prop, "group_monthly_cost", None), getattr(prop, "best_address", None)) if n
            )
            if extra:
                return (*self._deps, *extra)
        return self._deps

    @override
    def compute(self, *attempts) -> Attempt[MonthlyBaseline | None]:
        active = self._get_active_deps()
        if len(active) != len(attempts):
            raise ValueError(f"{self._id}: {len(active)} active deps but {len(attempts)} attempts")
        by_node = dict(zip(active, attempts, strict=True))

        winners = [
            str(node._id).split("/")[0]
            for node, att in by_node.items()
            if str(node._id).endswith("/status")
            and att is not None
            and att.succeeded
            and (att.value_or_none() or "").strip().lower() == CURRENT_STATUS
        ]
        if len(winners) != 1:
            # Zero current homes, or SEVERAL (an ambiguous state) — no
            # baseline either way; never mass-balance into one.
            return Attempt.succeeded(None)
        winner_rid = winners[0]

        prop = self._registry.get(winner_rid) if self._registry is not None else None
        if prop is None:
            return Attempt.succeeded(None)
        group_node = getattr(prop, "group_monthly_cost", None)
        if group_node is None:
            return Attempt.succeeded(None)
        group_att = by_node.get(group_node)
        if group_att is None or not group_att.succeeded:
            return Attempt.succeeded(None)
        value = group_att.value_or_none()
        if not isinstance(value, dict) or _as_figure(value.get("couple")) is None:
            return Attempt.succeeded(None)

        address_node = getattr(prop, "best_address", None)
        address = ""
        address_att = by_node.get(address_node) if address_node is not None else None
        if address_att is not None and address_att.succeeded:
            address = str(address_att.value_or_none() or "")

        breakdown = value.get("others_breakdown")
        rent_paid = breakdown.get("rent_paid") if isinstance(breakdown, dict) else None
        return Attempt.succeeded(
            MonthlyBaseline(
                rid=winner_rid,
                address=address,
                group_value=value,
                others_rent_paid=float(rent_paid or 0),
            )
        )

    @staticmethod
    @override
    def provenance_display_value(att: Any) -> Any:
        """Parent trees state the baseline as the address + figures — never
        the raw group dict (which would repeat through the whole tree)."""
        v = att.value_or_none() if att is not None else None
        if not isinstance(v, MonthlyBaseline):
            return "No current home" if v is None else str(v)
        parts = [f"Your home ({v.address})" if v.address else "Your home"]
        for side in ("couple", "others"):
            figure = _as_figure(v.group_value.get(side))
            if figure is not None and figure.value is not None:
                parts.append(f"{side} {_money_text(figure.value)}")
        return " · ".join(parts)


def current_home_node() -> CurrentHomeNode:
    """The app-wide singleton (built lazily on first access)."""
    global _CURRENT_HOME
    if _CURRENT_HOME is None:
        _CURRENT_HOME = CurrentHomeNode()
    return _CURRENT_HOME


def _reset() -> None:
    """Drop the singleton (test isolation): status dep sets and the
    registry pointer are per-world state, never shared across tests."""
    global _CURRENT_HOME
    _CURRENT_HOME = None


_CURRENT_HOME: CurrentHomeNode | None = None