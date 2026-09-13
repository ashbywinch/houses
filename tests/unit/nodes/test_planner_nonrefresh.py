"""Trips-only edit must not re-plan: the planner deps are address strings."""

import pytest

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.geopoint import GeoPoint
from houses.model.domain import PlaceOfInterest
from houses.nodes.transit import DestinationAddressNode, DriveNode, RouteOptions


@pytest.mark.asyncio
async def test_trips_only_edit_schedules_zero_planners():
    poi = UserInputNode("pnr_poi", PlaceOfInterest)
    poi.push(
        PlaceOfInterest(label="Office", address="Bracknell Rd, London", trips_per_week=1, weeks_per_year=46), "test"
    )
    location = UserInputNode("pnr_loc", GeoPoint)
    location.push(GeoPoint(51.45, -0.99), "test")
    address = DestinationAddressNode("pnr_address", place=poi)
    calls: list = []

    async def fake_drive(loc, dest):
        from money import Money
        from pint import Quantity

        from houses.model.domain import Commute, Person

        calls.append((loc, dest))
        return __import__("dag.attempt", fromlist=["Attempt"]).Attempt.succeeded(
            Commute(
                person=Person(name="T", has_car=True),
                label="Office",
                destination=__import__("houses.model.domain", fromlist=["PlaceOfInterest"]).PlaceOfInterest(
                    label="Office", address=dest
                ),
                duration=Quantity(20, "minute"),
                daily_cost=Money("5.50", "GBP"),
                mode="drive",
            )
        )

    drive = DriveNode(
        "pnr_drive",
        options=RouteOptions(best_location=location, poi=address, has_car=True, max_walk=30, route_fn=fake_drive),
    )
    await flush_processor()
    assert drive.latest_attempt().succeeded, drive.latest_attempt().error
    assert len(calls) == 1, calls
    # Trips-only edit: the address projection is value-identical, so the
    # planner must NOT be scheduled and must NOT re-plan.
    from dag.scheduler import get_scheduler

    poi.push(
        PlaceOfInterest(label="Office", address="Bracknell Rd, London", trips_per_week=3, weeks_per_year=46), "test"
    )
    # Push WITHOUT flushing: the planner must not even be scheduled.

    poi.push(
        PlaceOfInterest(label="Office", address="Bracknell Rd, London", trips_per_week=3, weeks_per_year=46), "test"
    )
    queue = get_scheduler()._queue  # type: ignore[attr-defined]  # test seam: assert the planner was never scheduled
    assert "pnr_drive" not in {e.node_id for e in queue._queue}, "trips-only edit scheduled the planner"
    await flush_processor()
    assert len(calls) == 1, f"trips-only edit re-planned the route: {calls}"
