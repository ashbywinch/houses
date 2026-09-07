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

from dataclasses import dataclass
from decimal import Decimal

CURRENT_STATUS = "current"


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
        couple = self.group_value.get("couple") or {}
        others = self.group_value.get("others")
        return {
            "rid": self.rid,
            "address": self.address,
            "couple": _wire_figure(couple),
            "others": _wire_figure(others) if isinstance(others, dict) and others.get("value") is not None else None,
            "others_rent_paid": self.others_rent_paid,
        }


def _figure_value(figure: object) -> object:
    """A group figure's amount — None when the figure is uncomputable."""
    return figure.get("value") if isinstance(figure, dict) else None


def _wire_figure(figure: dict) -> dict:
    return {"value": str(figure.get("value")), "approx": _is_approx(figure)}


def _is_approx(figure: object) -> bool:
    """The figure carries uncertainty (nonzero stddev)."""
    return isinstance(figure, dict) and float(figure.get("stddev") or 0) > 0


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


def _group_delta(own: object, base: object) -> dict | None:
    """One group's delta — null when EITHER side's figure is uncomputable."""
    own_value, base_value = _figure_value(own), _figure_value(base)
    if own_value is None or base_value is None:
        return None
    delta = Decimal(str(own_value)) - Decimal(str(base_value))
    return {"value": f"{delta:+.2f}", "approx": _is_approx(own) or _is_approx(base)}


def delta_vs_home(group_value: dict, baseline: MonthlyBaseline) -> dict:
    """The per-group delta shape for one candidate's group figures."""
    base = baseline.group_value
    return {
        "couple": _group_delta(group_value.get("couple"), base.get("couple")),
        "others": _group_delta(group_value.get("others"), base.get("others")),
    }


def outcome_delta_vs_home(
    group_value: dict, rid: str, baseline: MonthlyBaseline | None
) -> dict | None:
    """A what-if outcome's delta vs the REAL baseline (never the staged
    hypothetical one) — null without a baseline or for the baseline
    property itself."""
    if baseline is None or rid == baseline.rid:
        return None
    return delta_vs_home(group_value, baseline)


def _group_block(summary: dict) -> dict | None:
    """The ``{status, value, ...}`` group dict — top level on property
    summaries, under ``affordability`` on detail payloads."""
    group = summary.get("group_monthly_cost")
    if group is None:
        affordability = summary.get("affordability")
        group = affordability.get("group_monthly_cost") if isinstance(affordability, dict) else None
    return group if isinstance(group, dict) else None


async def attach(summary: dict, rid: str, registry) -> dict:
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
        delta = None if baseline is None or is_current else delta_vs_home(value, baseline)
        group["value"] = {**value, "delta_vs_home": delta}
    return summary
