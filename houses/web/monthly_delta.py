"""Monthly deltas vs the current home — the "extra vs your home" fields.

The baseline and the deltas are DAG citizens (docs/dag-library.md →
Design Rules → "Never live on read": a read serves the node's persisted
value; the DAG recalculates on a status write or a baseline re-price):

- ``CurrentHomeNode`` (settings/current_home) — THE current home: the
  single registry property whose comment status is 'current' AND whose
  group_monthly_cost computed a couple figure.
- ``DeltaVsHomeNode`` per property (``{rid}/delta_vs_home``) — candidate
  group cost minus the baseline's; None when there is no current home or
  the property IS the home.

``attach`` only projects the nodes onto the serialized payload at the
serialization boundary — it calculates nothing:

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
from typing import Any

from houses import property_registry as pr
from houses.nodes.current_home_node import current_home_node


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


async def attach(summary: MutableMapping[str, Any], rid: str) -> MutableMapping[str, Any]:
    """Project the current-home nodes onto a summary or detail payload.

    Mutates and returns *summary*. Reads the persisted node attempts (in
    memory, ~0): nothing is recomputed or re-scanned here — recalculation
    belongs to the DAG.
    """
    home = current_home_node()
    att = home.latest_attempt()
    descriptor = att.value_or_none() if att is not None and att.succeeded else None
    summary["is_current_home"] = descriptor is not None and descriptor.rid == rid
    summary["monthly_baseline"] = descriptor.to_wire() if descriptor is not None else None

    prop = pr.get_property(rid)
    delta = getattr(prop, "delta_vs_home", None) if prop is not None else None
    if delta is not None:
        delta_rec = await delta.to_json_value()
        delta_wire = delta_rec.get("value") if isinstance(delta_rec, dict) else None
        group = _group_block(summary)
        value = group.get("value") if group is not None else None
        if group is not None and isinstance(value, dict):
            # Fresh copy: the DAG node's own value object is never mutated
            # through the serialized one.
            group["value"] = {**value, "delta_vs_home": delta_wire}
    return summary