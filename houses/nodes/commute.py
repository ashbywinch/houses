from __future__ import annotations

import time
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from money import Money

from dag.attempt import Attempt
from dag.computed_node import ComputedNode
from dag.source_node import SourceNode
from houses.geo import GeoPoint
from houses.model.domain import Commute


def format_duration(minutes: int | None) -> str:
    """Match the old ``houses.web.card_data._dur`` format.

    Returns ``""`` for ``None``, ``"9m"`` for <60, ``"1h2"`` (no space),
    ``"2h"`` for exact hours.
    """
    if minutes is None:
        return ""
    if minutes < 60:
        return f"{minutes}m"
    h = minutes // 60
    r = minutes % 60
    return f"{h}h{r}" if r else f"{h}h"


def commute_colour(minutes: int | None, bracknell: bool = False) -> str:
    """Match the old ``houses.web.card_data.commute_colour`` thresholds."""
    if minutes is None:
        return "muted"
    if bracknell:
        return "good" if minutes < 30 else "warn" if minutes <= 60 else "bad"
    return "good" if minutes < 45 else "warn" if minutes <= 75 else "bad"


def _serialize_value(val: Any) -> Any:
    if isinstance(val, Money):
        return {"amount": float(val.amount), "currency": val.currency}
    if hasattr(val, "units") and hasattr(val, "magnitude"):
        return {"value": float(val.magnitude), "unit": str(val.units)}
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, timedelta):
        return val.total_seconds()
    if isinstance(val, Enum):
        return val.name.lower()
    if is_dataclass(val) and not isinstance(val, type):
        cls_name = type(val).__name__
        if cls_name == "CommuteResult":
            dc = val.daily_cost
            return {
                "duration": {"value": int(val.duration.magnitude), "unit": "minute"},
                "daily_cost": (
                    {"amount": float(dc.amount), "currency": str(dc.currency)}
                    if dc else {"amount": 0, "currency": "GBP"}
                ),
                "label": val.label,
                "mode": val.mode,
                "route_description": val.route_description,
                "is_child": val.is_child,
                "details": [_serialize_leg(leg) for leg in val.details],
            }
        result: dict[str, Any] = {}
        for f in fields(val):
            result[f.name] = _serialize_value(getattr(val, f.name))
        return result
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    return val


def _serialize_leg(leg) -> dict:
    mode = leg.mode.name.lower() if isinstance(leg.mode, Enum) else leg.mode
    return {
        "mode": mode,
        "duration": {"value": int(leg.duration.magnitude), "unit": "minute"},
        "line_name": leg.line_name,
        "destination": leg.destination,
    }


def commute_source_node(node_id: str) -> SourceNode[Commute]:
    return _CommuteSourceNode(node_id)


class _CommuteSourceNode(SourceNode[dict]):
    """A SourceNode that holds a raw Commute (not serialised to dict).

    Values are not persisted to the DB — they are ephemeral results
    pushed from the commute pipeline during enrichment.
    """

    def __init__(self, node_id: str) -> None:
        super().__init__(node_id, dict)

    def push(self, value: Commute, source_label: str = "") -> None:
        self._value = value
        self._source_label = source_label
        self._persisted_at = time.monotonic()
        self._db_created_at = datetime.now(UTC).isoformat()
        self.changed.emit()

    async def attempt(self) -> Attempt[Commute]:
        if self._value is not None:
            return Attempt.succeeded(self._value)
        return Attempt.pending()

    async def build_provenance(self):
        from dag.attempt import Provenance
        return Provenance.from_label(self._source_label)


class CommuteSelectorNode(ComputedNode[dict]):
    def __init__(self, node_id: str, *, origin, poi, transit_result, bus_result):
        super().__init__(
            node_id,
            dict,
            (origin, poi, transit_result, bus_result),
        )

    def compute(self, origin: Attempt[GeoPoint],
                poi: Attempt[str],
                transit: Attempt[dict],
                bus: Attempt[dict]) -> Attempt[dict]:
        if not origin.succeeded or not poi.succeeded:
            return self._impossible(
                {"origin": origin, "poi": poi},
            )
        if transit.succeeded:
            return transit
        if bus.succeeded:
            return bus
        return self._impossible(
            {"transit_result": transit, "bus_result": bus},
        )

    async def to_json(self) -> dict:
        attempt = await self.attempt()
        result: dict = {
            "status": attempt.status,
            "value": _serialize_value(attempt.value_or_none()) if attempt.succeeded else None,
        }
        if attempt.impossible:
            result["error"] = attempt.error
        result["provenance"] = (await self.build_provenance()).to_dict()
        return result
