from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from money import Money

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode
from dag.user_input_node import UserInputNode
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


def commute_input_node(node_id: str) -> UserInputNode[Commute]:
    return _CommuteInputNode(node_id)


class _CommuteInputNode(UserInputNode[dict]):
    """A UserInputNode that holds a raw Commute (not serialised to dict).

    Values are not persisted to the DB — they are ephemeral results
    pushed from the commute pipeline during enrichment.
    """

    def __init__(self, node_id: str) -> None:
        super().__init__(node_id, dict)

    def push(self, value: Commute, source_label: str = "") -> None:
        self._value = value
        self._source_label = source_label
        self._persisted_at = datetime.now(UTC)
        self._db_created_at = datetime.now(UTC).isoformat()
        self.changed.emit()
    async def attempt(self) -> Attempt[Commute]:
        if self._value is not None:
            return Attempt.succeeded(self._value)
        return Attempt.pending()

    async def build_provenance(self):
        return Provenance.from_label(self._source_label)


class RailFareNode(DerivedNode[dict]):
    """Computes the National Rail fare for a commute when TfL has no price."""

    def __init__(self, node_id: str, *, commute_node, best_location, person_name):
        super().__init__(node_id, dict, (commute_node, best_location, person_name))

    async def compute(self, commute_attempt: Attempt[dict],
                       location_attempt: Attempt[GeoPoint],
                       name_attempt: Attempt[str]) -> Attempt[dict]:
        if not commute_attempt.succeeded:
            return commute_attempt
        val = commute_attempt.value_or_none() or {}
        dc = val.get("daily_cost") or {}
        if float(dc.get("amount", 0)) > 0:
            return commute_attempt
        from houses.commute import Commute as OldCommute
        from houses.rail_fares import enrich_rail_fares
        person_name = (name_attempt.value_or_none() or "").lower()
        dest_postcode = val.get("label", "")
        old = OldCommute("", dest_postcode, val.get("duration", {}).get("value"), None)
        dummy = OldCommute("", "", None, None)
        simon = old if person_name == "simon" else dummy
        lorena = old if person_name == "lorena" else dummy
        enriched, _ = await enrich_rail_fares({person_name}, dest_postcode, dest_postcode, simon, lorena)
        result = enriched if person_name == "simon" else _
        if result.daily_cost_gbp is not None and float(result.daily_cost_gbp.amount) > 0:
            val["daily_cost"] = {"amount": float(result.daily_cost_gbp.amount), "currency": "GBP"}
        return Attempt.succeeded(val)

class CommuteSelectorNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, origin, poi, transit_result, bus_result,
                 is_child: bool = False, rail_fare_node=None):
        deps = (origin, poi, transit_result, bus_result)
        super().__init__(node_id, dict, deps)
        self._is_child = is_child
        self._rail_fare_node = rail_fare_node
    async def compute(self, origin: Attempt[GeoPoint],
                      poi: Attempt[str],
                      transit: Attempt[dict],
                      bus: Attempt[dict]) -> Attempt[dict]:
        if not origin.succeeded or not poi.succeeded:
            return self._impossible({"origin": origin, "poi": poi})

        selected = None
        if transit.succeeded:
            selected = transit
        elif bus.succeeded:
            selected = bus

        if selected is not None:
            val = selected.value_or_none() or {}
            if isinstance(val, dict):
                dc = val.get("daily_cost") or {}
                if dc.get("amount", 0) == 0 and self._rail_fare_node is not None:
                    rf_attempt = await self._rail_fare_node.attempt()
                    if rf_attempt.succeeded:
                        return rf_attempt
            return selected

        return self._impossible({"transit_result": transit, "bus_result": bus})

    async def to_json(self) -> dict:
        attempt = await self.attempt()
        result: dict = {
            "status": attempt.status,
            "value": _serialize_value(attempt.value_or_none()) if attempt.succeeded else None,
            "is_child": self._is_child,
        }
        result["succeeded"] = attempt.succeeded
        result["pending"] = attempt.pending
        result["impossible"] = attempt.impossible
        if attempt.impossible:
            result["error"] = attempt.error
        result["provenance"] = (await self.build_provenance()).to_dict()
        return result
