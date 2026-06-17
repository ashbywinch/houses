"""Commute ComputedNodes for the reactive DAG.

Each Person × PlaceOfInterest gets a CommuteSelectorNode that picks
the best available commute from transit and bus results. Transit and
bus results are pushed to SourceNodes by the existing enrichment modules.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from money import Money

from dag.attempt import Attempt
from dag.computed_node import ComputedNode
from dag.source_node import SourceNode
from houses.geo import GeoPoint
from houses.model.domain import Commute, PlaceOfInterest


def _serialize_value(val: Any) -> Any:
    """Recursively serialise a value tree to JSON-friendly types.

    Handles Money, pint Quantity, dataclasses, enums, and standard collections.
    """
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
        result: dict[str, Any] = {}
        for f in fields(val):
            result[f.name] = _serialize_value(getattr(val, f.name))
        return result
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    return val


def commute_source_node(node_id: str) -> SourceNode[Commute]:
    """Create a SourceNode for Commute values.

    Uses a plain dict value_type internally and overrides the adapter
    to avoid pydantic issues with Money/pint types.
    """
    return _CommuteSourceNode(node_id)


class _CommuteSourceNode(SourceNode[dict]):
    """A SourceNode that stores Commute objects but bypasses TypeAdapter for them."""

    def __init__(self, node_id: str) -> None:
        super().__init__(node_id, dict)

    def push(self, value: Commute, provenance) -> None:
        self._value = value
        self._provenance = provenance
        self.changed.emit()

    def attempt(self) -> Attempt[Commute]:
        if self._value is not None:
            return Attempt.succeeded(self._value, self._provenance)
        return Attempt.impossible("not set")


class CommuteSelectorNode(ComputedNode[dict]):
    """Selects the best commute from transit and bus results.

    Priority: transit > bus > impossible.
    Requires origin and POI to be resolved.
    Serialises Commute values via custom _serialize_value to support
    Money and pint types that pydantic's TypeAdapter cannot handle.
    """

    def __init__(self, node_id: str, *, origin, poi, transit_result, bus_result):
        super().__init__(
            node_id,
            dict,
            (origin, poi, transit_result, bus_result),
        )

    def compute(self, origin: Attempt[GeoPoint],
                poi: Attempt[PlaceOfInterest],
                transit: Attempt[Commute],
                bus: Attempt[Commute]) -> Attempt[Commute]:
        if not origin.is_succeeded or not poi.is_succeeded:
            return self._impossible(
                {"origin": origin, "poi": poi},
            )
        if transit.is_succeeded:
            return transit
        if bus.is_succeeded:
            return bus
        return self._impossible(
            {"transit_result": transit, "bus_result": bus},
        )

    def to_json(self) -> dict:
        attempt = self.attempt()
        result: dict[str, Any] = {
            "succeeded": attempt.is_succeeded,
            "provenance": self._provenance_to_json(attempt.provenance),
        }
        if attempt.is_succeeded:
            result["value"] = _serialize_value(attempt.value_or_none())
            result["error"] = None
        else:
            result["value"] = None
            result["error"] = attempt._error
        return result
