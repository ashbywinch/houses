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

The group block is a ``dag.node.NodeValue`` that has already been serialized
into the payload; ``attach`` rebuilds that record from the payload's own wire
and writes the record back into the slot it read the wire from, so the delta
it adds lands in the payload the endpoint serves.

Zero or several current homes, or an uncomputable baseline figure →
``monthly_baseline`` is null EVERYWHERE and deltas are null: cards fall
back to today's totals. Never zeros-as-meaning. This module stays free of
FastAPI imports so the wire shapes test as plain data.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from dag.node import NodeValue
from houses import property_registry as pr
from houses.nodes.current_home_node import current_home_node


def _group_slot(summary: MutableMapping[str, Any]) -> MutableMapping[str, Any] | None:
    """The payload mapping that owns the ``group_monthly_cost`` key — the
    payload itself on property summaries, its ``affordability`` mapping on
    detail payloads; ``None`` when the payload carries no group block.

    The block is read back through this slot, so the lookup that finds the
    wire and the one that writes it back can never disagree.
    """
    group = summary.get("group_monthly_cost")
    if isinstance(group, dict):
        return summary
    affordability = summary.get("affordability")
    if isinstance(affordability, MutableMapping) and isinstance(affordability.get("group_monthly_cost"), dict):
        return affordability
    return None


def _add_delta(summary: MutableMapping[str, Any], delta_wire: Any) -> None:
    """Add ``delta_vs_home`` to the payload's group block, in place.

    The block is the group node's own ``to_json_value`` wire, so it is
    rebuilt as the DAG's ``NodeValue`` record and the record is written back
    into the slot it was read from. The write-back lays the rebuilt wire OVER
    the block's own wire rather than replacing it: ``NodeValue.to_dict()``
    reproduces the block's keys in the block's order (the wire came from that
    same dump — declaration order, the same ``exclude_if`` conditions), but a
    derived node's wire is that dump PLUS the lifecycle keys
    ``DerivedNode._enrich_json`` appends afterwards
    (``retry_at``/``retry_count``/``stale``), which the record does not
    carry; replacing the wire outright would drop them and change the
    payload's key set.
    """
    slot = _group_slot(summary)
    if slot is None:
        return
    wire = slot["group_monthly_cost"]
    record = NodeValue.model_validate(wire)
    if not isinstance(record.value, dict):
        return
    # Fresh copy: the DAG node's own value object is never mutated through
    # the serialized one.
    record.value = {**record.value, "delta_vs_home": delta_wire}
    slot["group_monthly_cost"] = {**wire, **record.to_dict()}


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
    summary["monthly_baseline"] = descriptor.to_wire().to_dict() if descriptor is not None else None

    prop = pr.get_property(rid)
    delta = getattr(prop, "delta_vs_home", None) if prop is not None else None
    if delta is not None:
        delta_rec = await delta.to_json_value()
        delta_wire = delta_rec.get("value") if isinstance(delta_rec, dict) else None
        _add_delta(summary, delta_wire)
    return summary