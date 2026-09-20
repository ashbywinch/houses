"""The Commute contract: every value states its destination, with its
frequency.

Failing first (live 2026-09-16): a Commute value whose destination was
absent (or carried a null frequency) validated in memory but could not
be re-validated once persisted — `destination.trips_per_week` came back
null. The current home's `commute_breakdown` then went impossible, its
`group_monthly_cost` followed, no baseline resolved, and both the index
and the detail page dropped back to absolute costs, losing the delta
display.

So: the destination is a required field whose frequency is an int, and a
commute's stored form re-validates unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest
from money import Money
from pint import Quantity
from pydantic import TypeAdapter, ValidationError

import dag.user_input_node  # noqa: F401 — register pydantic schemas
from dag.attempt import Attempt
from dag.user_input_node import UserInputNode
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.geopoint import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.nodes.commute import CommuteSelectorNode, CommuteSelectorOptions
from houses.nodes.transit import DriveNode, RouteOptions
from tests.unit.conftest import flush_all

_HOME_LAT = 51.45
_HOME_LON = -0.99
_ORIGIN = GeoPoint(_HOME_LAT, _HOME_LON)
_DESTINATION_ADDRESS = "RG12 8YA"
_DRIVE_MINUTES = 20
_DRIVE_COST = "5.50"
_DRIVE_DISTANCE_KM = 12.0
_WEEKS_PER_YEAR = 46
_FREQUENCY = 1

_ADAPTER = TypeAdapter(Commute)


def _commute(*, destination: Any, label: str = "Drive") -> Commute:
    return Commute(
        person=Person(name="Test", has_car=True),
        label=label,
        destination=destination,
        duration=Quantity(_DRIVE_MINUTES, "minute"),
        daily_cost=Money(_DRIVE_COST, "GBP"),
    )


def _drive_node(node_id: str, origin: UserInputNode, address: UserInputNode) -> DriveNode:
    async def _route(location: GeoPoint, dest: str) -> Attempt[Commute]:
        return Attempt.succeeded(
            _commute(destination=PlaceOfInterest(label="", address=dest), label="Drive")
        )

    return DriveNode(
        node_id,
        options=RouteOptions(
            best_location=origin,
            poi=address,
            has_car=True,
            max_walk=30,
            route_fn=_route,
        ),
    )


def test_a_destination_less_commute_does_not_validate():
    """The stored shape rejects a missing destination and a null frequency.

    This is the seam the live regression crossed: `destination=None` and
    `destination.trips_per_week=None` both validated, so bad values were
    written and then failed to reload.
    """
    valid = _ADAPTER.dump_python(
        _commute(destination=PlaceOfInterest(label="Bracknell", address=_DESTINATION_ADDRESS)),
        mode="json",
    )
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({**valid, "destination": None})

    no_frequency = {**valid, "destination": {**valid["destination"], "trips_per_week": None, "weeks_per_year": None}}
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(no_frequency)


def test_a_persisted_commute_revalidates_unchanged():
    """A planner-built commute stores its destination and reloads identical."""
    origin = UserInputNode("cd_origin", GeoPoint)
    origin.push(_ORIGIN, "test")
    address = UserInputNode("cd_address", str)
    address.push(_DESTINATION_ADDRESS, "test")
    drive = _drive_node("cd_drive", origin, address)
    flush_all()

    value = drive.latest_attempt().value_or_none()
    assert value is not None, drive.latest_attempt().error
    assert value.destination is not None, "a planner value states the destination it routed to"

    stored = _ADAPTER.dump_python(value, mode="json")
    assert stored["destination"] is not None, f"the stored destination must survive, got {stored!r}"
    assert isinstance(stored["destination"].get("trips_per_week"), int), (
        f"the stored destination must carry a frequency, got {stored['destination']!r}"
    )
    assert _ADAPTER.validate_python(stored) == value, "a stored commute re-validates unchanged"


def test_every_value_in_the_commute_chain_carries_a_destination():
    """The selector's value states the place, with its frequency."""
    origin = UserInputNode("cd2_origin", GeoPoint)
    origin.push(_ORIGIN, "test")
    address = UserInputNode("cd2_address", str)
    address.push(_DESTINATION_ADDRESS, "test")
    place = UserInputNode("cd2_place", PlaceOfInterest)
    place.push(
        PlaceOfInterest(
            label="Bracknell",
            address=_DESTINATION_ADDRESS,
            trips_per_week=_FREQUENCY,
            weeks_per_year=_WEEKS_PER_YEAR,
        ),
        "test",
    )
    drive = _drive_node("cd2_drive", origin, address)
    selector = CommuteSelectorNode(
        "cd2_selector",
        options=CommuteSelectorOptions(
            origin=origin,
            poi=place,
            transit_result=drive,  # a stand-in: the drive result wins on duration
            drive_result=drive,
        ),
    )
    flush_all()

    for node in (drive, selector):
        value = node.latest_attempt().value_or_none()
        assert value is not None, (node._id, node.latest_attempt().error)
        assert value.destination is not None, f"{node._id} produced a value with no destination"
        assert isinstance(value.destination.trips_per_week, int), (
            f"{node._id} produced a destination with no frequency: {value.destination!r}"
        )


def test_an_infeasible_commute_still_states_its_destination():
    """No car → infeasible, and the value still carries its destination.

    An infeasible commute is a VALUE (it flows through the totals and the
    provenance), so it is subject to the same contract as any other.
    """
    origin = UserInputNode("cd4_origin", GeoPoint)
    origin.push(_ORIGIN, "test")
    address = UserInputNode("cd4_address", str)
    address.push(_DESTINATION_ADDRESS, "test")
    node = DriveNode(
        "cd4_drive",
        options=RouteOptions(best_location=origin, poi=address, has_car=False, max_walk=30),
    )
    flush_all()

    value = node.latest_attempt().value_or_none()
    assert value is not None, node.latest_attempt().error
    assert value.infeasible, "a person with no car does not drive"
    assert value.destination is not None, "an infeasible commute still states its destination"
    assert isinstance(value.destination.trips_per_week, int), value.destination


def test_a_leg_bearing_commute_round_trips_through_storage():
    """The wiring the delta chain depends on: legs and cost groups survive."""
    commute = Commute(
        person=Person(name="Test", has_car=True),
        label="Drive",
        destination=PlaceOfInterest(
            label="Bracknell",
            address=_DESTINATION_ADDRESS,
            trips_per_week=_FREQUENCY,
            weeks_per_year=_WEEKS_PER_YEAR,
        ),
        duration=Quantity(_DRIVE_MINUTES, "minute"),
        daily_cost=Money(_DRIVE_COST, "GBP"),
        mode="drive",
        _details=(
            CostGroup(
                legs=(
                    JourneyLeg(
                        mode=LegMode.DRIVE,
                        duration=Quantity(_DRIVE_MINUTES, "minute"),
                        distance=Quantity(_DRIVE_DISTANCE_KM, "km"),
                        end_station=_DESTINATION_ADDRESS,
                    ),
                ),
                operator="",
                cost=Money(_DRIVE_COST, "GBP"),
            ),
        ),
    )
    stored = _ADAPTER.dump_python(commute, mode="json")
    assert _ADAPTER.validate_python(stored) == commute
