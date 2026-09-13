"""Monthly deltas vs the current home — the "extra vs your home" fields.

THE baseline is the single registry property whose comment status is
'current' (case/space-insensitive) AND whose group_monthly_cost computed a
couple figure. Every consumer attaches the same wire fields at the
serialization boundary (never inside the DAG node):

- ``is_current_home`` — the property IS the current home
- ``monthly_baseline`` — the baseline's identity + group figures, or null
- ``group_monthly_cost.value.delta_vs_home`` — per-group candidate − baseline,
  explicit sign, GBP/month, 2dp

Zero or several current homes, or an uncomputable baseline figure →
``monthly_baseline`` is null EVERYWHERE and deltas are null: cards fall
back to today's totals. Never zeros-as-meaning. This module stays free of
FastAPI imports so the wire shapes test as plain data.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

CURRENT_STATUS = "current"


@dataclass(frozen=True)
class _RawFigure:
    """One raw DAG group figure ({value, stddev}), ingested at the edge."""

    value: object
    stddev: float

    @classmethod
    def from_dict(cls, raw: dict) -> _RawFigure:
        return cls(value=raw.get("value"), stddev=float(raw.get("stddev") or 0))


def _as_figure(raw: object) -> _RawFigure | None:
    """The ingested figure when *raw* is its dict shape, else None."""
    return _RawFigure.from_dict(raw) if isinstance(raw, dict) else None


def _figure_or_empty(raw: object) -> _RawFigure:
    """The ingested figure — an empty one when *raw* is absent (mirrors the
    historical ``or {}``: a missing couple figure serializes as "None")."""
    figure = _as_figure(raw)
    return figure if figure is not None else _RawFigure(value=None, stddev=0.0)


@dataclass(frozen=True)
class _FigureWire:
    """One group figure as serialized: {value, approx} (wire shape)."""

    value: str
    approx: bool

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return {"value": self.value, "approx": self.approx}


def _delta_provenance(
    *,
    own: _RawFigure | None,
    base: _RawFigure | None,
    delta: str,
    candidate_label: str,
    baseline_address: str,
) -> dict:
    """The delta's derivation as a provenance dict — candidate − baseline.

    A hand-written arithmetic tooltip (the old title=) is not provenance:
    every other number explains itself through the standard ⓘ, and the
    monthly difference must too. The formula names both input figures
    (candidate total, baseline home total) and the signed result; the
    description names the baseline home so the reader knows WHAT was
    subtracted.
    """
    own_str = str(own.value) if own is not None and own.value is not None else "?"
    base_str = str(base.value) if base is not None and base.value is not None else "?"
    approx_note = " (≈ = council tax estimated)" if _is_approx(own) or _is_approx(base) else ""
    short = (baseline_address.split(",")[0] or baseline_address).strip()
    return {
        "label": "Monthly difference vs your home",
        "description": f"Candidate monthly total minus your home ({short}).{approx_note}",
        "sourceType": "calc",
        "formula": {
            "lines": [
                {"label": f"This property ({candidate_label})", "value": f"£{own_str}/mo"},
                {"label": f"Your home ({short})", "value": f"£{base_str}/mo"},
            ],
            "result": f"{delta}/mo",
        },
        "sources": {},
    }


@dataclass(frozen=True)
class MonthlyBaseline:
    """The resolved current home: identity plus its raw group figures.

    ``group_value`` is the group_monthly_cost attempt value dict (couple and
    others, each ``{value, stddev}``) — kept raw so delta computation can
    read the stddevs; ``to_wire`` projects the contract shape.
    """

    rid: str
    address: str
    group_value: dict
    others_rent_paid: float

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_wire(self) -> dict:
        couple = _figure_or_empty(self.group_value.get("couple"))
        others = _as_figure(self.group_value.get("others"))
        # lucidlint: ignore record-shape to_wire construction IS the serialization boundary (coding-standards.md)
        return {
            "rid": self.rid,
            "address": self.address,
            "couple": _wire_figure(couple).to_dict(),
            "others": _wire_figure_or_none(others),
            "others_rent_paid": self.others_rent_paid,
        }


def _figure_value(figure: object) -> object:
    """A group figure's amount — None when the figure is uncomputable."""
    return figure.get("value") if isinstance(figure, dict) else None

def _wire_figure(figure: _RawFigure) -> _FigureWire:
    return _FigureWire(value=str(figure.value), approx=_is_approx(figure))


# lucidlint: ignore record-shape the serialized figure dict IS the wire shape — {value, approx} owned by _FigureWire,
# None when the figure is uncomputable (coding-standards.md)
def _wire_figure_or_none(figure: _RawFigure | None) -> dict | None:
    """The figure's serialized shape — None when it is uncomputable."""
    if figure is None or figure.value is None:
        return None
    return _wire_figure(figure).to_dict()
def _is_approx(figure: _RawFigure | None) -> bool:
    """The figure carries uncertainty (nonzero stddev)."""
    return figure is not None and figure.stddev > 0





def _status_is_current(prop) -> bool:
    """comment_status == 'current', case/space-insensitive — the same
    idiom as the current-homes route. A property without the node (a
    minimal/fake registry entry) is never the current home."""
    node = getattr(prop, "comment_status", None)
    if node is None:
        return False
    att = node.latest_attempt()
    return att.succeeded and (att.value_or_none() or "").strip().lower() == CURRENT_STATUS


def _address_of(prop) -> str:
    """best_address succeeded value, else the rid."""
    att = prop.best_address.latest_attempt()
    value = att.value_or_none() if att.succeeded else None
    return str(value) if value else prop.rid


def resolve_baseline(registry) -> MonthlyBaseline | None:
    """THE current home: exactly one current-status property whose group
    figure computed a couple value — else None (zero or several current
    homes, or the current home's figure is uncomputable)."""
    homes = [
        rid
        for rid in registry.list_properties()
        if (prop := registry.get(rid)) is not None and _status_is_current(prop)
    ]
    if len(homes) != 1:
        return None
    prop = registry.get(homes[0])
    node = getattr(prop, "group_monthly_cost", None)
    if node is None:
        return None
    att = node.latest_attempt()
    value = att.value_or_none() if att.succeeded else None
    if not isinstance(value, dict) or _figure_value(value.get("couple")) is None:
        return None
    breakdown = value.get("others_breakdown")
    rent_paid = breakdown.get("rent_paid") if isinstance(breakdown, dict) else None
    return MonthlyBaseline(
        rid=homes[0],
        address=_address_of(prop),
        group_value=value,
        others_rent_paid=float(rent_paid or 0),
    )

def _group_delta(
    own: _RawFigure | None,
    base: _RawFigure | None,
    *,
    candidate_label: str,
    baseline_address: str,
) -> _FigureWire | None:
    """One group's delta — null when EITHER side's figure is uncomputable."""
    if own is None or base is None or own.value is None or base.value is None:
        return None
    delta = Decimal(str(own.value)) - Decimal(str(base.value))
    return _FigureWire(value=f"{delta:+.2f}", approx=_is_approx(own) or _is_approx(base))


@dataclass(frozen=True)
class _GroupDeltasJson:
    """The per-group delta block {couple, others} (wire shape)."""

    couple: _FigureWire | None
    others: _FigureWire | None

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return {
            "couple": self.couple.to_dict() if self.couple is not None else None,
            "others": self.others.to_dict() if self.others is not None else None,
        }


# lucidlint: ignore record-shape the per-group delta dict IS the wire shape — owned by _GroupDeltasJson; tests pin the
# dict-returning signature (delta["couple"] indexing), so the record serializes at the boundary (coding-standards.md)
def delta_vs_home(
    group_value: Mapping[str, object],
    baseline: MonthlyBaseline,
    *,
    candidate_label: str = "",
    candidate_address: str = "",
) -> dict:
    """The per-group delta shape for one candidate's group figures.

    ``group_value`` is the raw group-monthly-cost DAG value dict (couple and
    others figures; the degenerate no-adults shapes omit the labels) — tests
    pin the dict signature; the figures are ingested via ``_as_figure`` at
    this edge. Each side's delta carries its derivation as ``provenance``
    (candidate − baseline, both figures named) for the standard ⓘ.
    """
    base = baseline.group_value
    label = candidate_label or candidate_address or "this property"
    deltas = _GroupDeltasJson(
        couple=_group_delta(
            _as_figure(group_value.get("couple")),
            _as_figure(base.get("couple")),
            candidate_label=label,
            baseline_address=baseline.address,
        ),
        others=_group_delta(
            _as_figure(group_value.get("others")),
            _as_figure(base.get("others")),
            candidate_label=label,
            baseline_address=baseline.address,
        ),
    ).to_dict()
    for side in ("couple", "others"):
        wire = deltas.get(side)
        if not isinstance(wire, dict):
            continue
        own = _as_figure(group_value.get(side))
        base_fig = _as_figure(base.get(side))
        if own is None or base_fig is None or own.value is None or base_fig.value is None:
            continue
        wire["provenance"] = _delta_provenance(
            own=own,
            base=base_fig,
            delta=wire["value"],
            candidate_label=label,
            baseline_address=baseline.address,
        )
    return deltas


# lucidlint: ignore record-shape the extracted group block IS part of the variable-keyed property payload — passthrough
# of a wire subsection, not a fixed record shape of its own (coding-standards.md)
def _group_block(summary: Mapping[str, object]) -> dict | None:
    """The ``{status, value, ...}`` group dict — top level on property
    summaries, under ``affordability`` on detail payloads."""
    group = summary.get("group_monthly_cost")
    if group is None:
        affordability = summary.get("affordability")
        group = affordability.get("group_monthly_cost") if isinstance(affordability, dict) else None
    return group if isinstance(group, dict) else None


# summary is the variable-keyed property wire payload (see _group_block) — mutated and returned in place, no fixed
# record exists across property states; the delta fields are attached at the serialization edge
async def attach(summary: MutableMapping[str, Any], rid: str, registry) -> MutableMapping[str, Any]:
    """Attach the three monthly-delta fields to a summary or detail payload.

    Mutates and returns *summary*. The delta is inserted into a fresh copy
    of the group value dict (``{**value, ...}``) so the DAG node's own
    value object is never mutated through the serialized one.
    """
    prop = registry.get(rid)
    is_current = prop is not None and _status_is_current(prop)
    baseline = resolve_baseline(registry)
    summary["is_current_home"] = is_current
    summary["monthly_baseline"] = baseline.to_wire() if baseline is not None else None
    group = _group_block(summary)
    value = group.get("value") if group is not None else None
    if group is not None and isinstance(value, dict):
        if baseline is None or is_current:
            delta = None
        else:
            labels = value.get("couple_label"), value.get("others_label")
            candidate_label = str(labels[0] or labels[1] or "")
            candidate_address = _address_of(prop) if prop is not None else rid
            delta = delta_vs_home(
                value,
                baseline,
                candidate_label=candidate_label,
                candidate_address=candidate_address,
            )
        group["value"] = {**value, "delta_vs_home": delta}
    return summary
