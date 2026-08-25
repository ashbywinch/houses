"""The commute selector must fall back to drive/walk when transit has
*no route* — modelled as a succeeded-infeasible commute, never as an
impossible attempt. A genuinely failed transit API call is impossible
and propagates (the framework short-circuits, as it should)."""
from __future__ import annotations

from typing import override

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.geopoint import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.nodes.commute import CommuteSelectorNode, CommuteSelectorOptions


def _mw(value: int):
    """A fixed max-walk input node."""
    from dag.user_input_node import UserInputNode

    node = UserInputNode("_mw", int)
    node.push(value, "test")
    return node

class _FixedNode(DerivedNode[Commute]):
    def __init__(self, node_id: str, attempt: Attempt[Commute]):
        super().__init__(node_id, Commute, ())
        self._att = attempt

    @override
    async def attempt(self):
        return self._att

    @override
    def latest_attempt(self):
        return self._att

    @override
    def compute(self, *dep_attempts: Attempt) -> Attempt[Commute]:
        raise AssertionError("fixed node should not compute")

def _commute(duration_min: int, cost_gbp: float, *, infeasible: bool = False) -> Commute:
    return Commute(
        person=Person(name="Simon", has_car=True),
        label="x",
        destination=PlaceOfInterest(label="x", address="SW1V 2QQ"),
        duration=Quantity(duration_min, "minute"),
        daily_cost=Money(str(cost_gbp), "GBP"),
        mode="transit",
        infeasible=infeasible,
    )

def _selector(node_id: str, transit: Attempt, walk: Attempt | None, drive: Attempt | None):
    origin = UserInputNode[GeoPoint](f"{node_id}_origin", GeoPoint)
    origin.push(GeoPoint(51.5, -0.1), "test")
    poi = UserInputNode[str](f"{node_id}_poi", str)
    poi.push("RG12 8YA", "test")
    return CommuteSelectorNode(
        f"{node_id}/commute",
        options=CommuteSelectorOptions(
            origin=origin,
            poi=poi,
            transit_result=_FixedNode(f"{node_id}_transit", transit),
            walk_result=None if walk is None else _FixedNode(f"{node_id}_walk", walk),
            drive_result=None if drive is None else _FixedNode(f"{node_id}_drive", drive),
            is_child=False,
            max_walk_node=_mw(30),
        ),
    )

@pytest.mark.asyncio
async def test_selector_picks_drive_when_transit_has_no_route():
    """Transit succeeded-infeasible + walk succeeded-infeasible + drive
    succeeded → the framework runs compute and the selector picks drive."""
    no_route = Attempt.succeeded(_commute(0, 0, infeasible=True))
    selector = _selector(
        "fb1",
        transit=no_route,
        walk=Attempt.succeeded(_commute(999, 0, infeasible=True)),
        drive=Attempt.succeeded(_commute(35, 5)),
    )
    await flush_processor()
    a = await selector.attempt()
    assert a.succeeded, f"selector should pick drive, got: {a.status}: {a.error}"
    val = a.value_or_none()
    assert val is not None
    assert val.duration.magnitude == 35
    assert not val.infeasible

@pytest.mark.asyncio
async def test_selector_picks_walk_when_transit_has_no_route():
    """Transit no-route + walk succeeded → walk wins (fastest feasible)."""
    no_route = Attempt.succeeded(_commute(0, 0, infeasible=True))
    selector = _selector(
        "fb2",
        transit=no_route,
        walk=Attempt.succeeded(_commute(15, 0)),
        drive=Attempt.succeeded(_commute(35, 5)),
    )
    await flush_processor()
    a = await selector.attempt()
    assert a.succeeded, f"selector should pick walk, got: {a.status}: {a.error}"
    val = a.value_or_none()
    assert val is not None
    assert val.duration.magnitude == 15

@pytest.mark.asyncio
async def test_selector_impossible_when_all_routes_infeasible():
    """Every alternative infeasible → selector impossible (Choose finds
    no winner), not a crash."""
    selector = _selector(
        "fb3",
        transit=Attempt.succeeded(_commute(0, 0, infeasible=True)),
        walk=Attempt.succeeded(_commute(0, 0, infeasible=True)),
        drive=Attempt.succeeded(_commute(0, 0, infeasible=True)),
    )
    await flush_processor()
    a = await selector.attempt()
    assert a.impossible

@pytest.mark.asyncio
async def test_selector_impossible_when_transit_api_fails():
    """A genuinely failed transit call (impossible attempt) propagates
    through the strict framework — the selector must NOT fall back."""
    selector = _selector(
        "fb4",
        transit=Attempt.impossible("could not route transit"),
        walk=Attempt.succeeded(_commute(15, 0)),
        drive=Attempt.succeeded(_commute(35, 5)),
    )
    await flush_processor()
    a = await selector.attempt()
    assert a.impossible, "genuine transit failure must propagate, not fall back"

@pytest.mark.asyncio
async def test_selector_keeps_walk_when_it_is_the_only_option():
    """A feasible walk that exceeds max_walk must still be returned when
    every other alternative is infeasible — a 37-minute walk to school is
    a real commute, not 'no alternative selected'."""
    origin = UserInputNode[GeoPoint]("lro_origin", GeoPoint)
    origin.push(GeoPoint(51.5968644, -1.2957186), "test")
    poi = UserInputNode[str]("lro_poi", str)
    poi.push("51.6053205,-1.2749334", "test")

    transit = _FixedNode("lro_transit", Attempt.succeeded(_commute(0, 0, infeasible=True)))
    walk = _FixedNode("lro_walk", Attempt.succeeded(_commute(37, 0)))
    selector = CommuteSelectorNode(
        "lro/commute",
        options=CommuteSelectorOptions(
            origin=origin,
            poi=poi,
            transit_result=transit,
            walk_result=walk,
            drive_result=None,  # child — no car
            is_child=True,
            max_walk_node=_mw(30),
        ),
    )
    await flush_processor()
    a = await selector.attempt()
    assert a.succeeded, f"the only feasible option (walk 37m) must be returned, got: {a.status}: {a.error}"
    val = a.value_or_none()
    assert val is not None
    assert val.duration.magnitude == 37
