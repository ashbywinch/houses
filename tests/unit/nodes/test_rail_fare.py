"""Tests for graceful rail fare handling when transit has no train legs."""

from __future__ import annotations

import pytest
from money import Money
from pint import Quantity

from dag.user_input_node import UserInputNode
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest


def _make_commute(
    duration_min: int = 32,
    cost_gbp: float = 4.50,
    mode: str = "transit",
    details: tuple[CostGroup, ...] | None = None,
) -> Commute:
    office = PlaceOfInterest("Office", "SW1V 2QQ")
    person = Person("Simon", True, places_of_interest=(office,))
    if details is not None:
        pass
    else:
        legs = (JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(30, "minute"), end_station="Paddington"),)
        details = (CostGroup(legs=legs, operator="TfL", cost=Money(str(cost_gbp), "GBP")),)
    return Commute(
        person=person,
        label=office.label,
        destination=office,
        duration=Quantity(duration_min, "minute"),
        daily_cost=Money(str(cost_gbp), "GBP"),
        mode=mode,
        _details=details,
    )


@pytest.mark.asyncio
async def test_rail_fare_graceful_when_no_train_legs():
    """Regression: two commutes (Simon/Pimlico, Lorena/Aldgate) had
    disappeared from the property page — the rail fare node returned
    'terminal station not found in route legs' for bus-only transit
    routes (no train/tube leg), and the impossible result cascaded into
    the whole commute being dropped from the card.

    Contract: a transit commute with cost=0 and no train/tube legs must
    stay AVAILABLE on the property card — the rail fare node returns
    the original commute without the fare instead of impossible."""
    from houses.nodes.rail_fare_node import RailFareNode

    transit = UserInputNode("tr_grace", Commute)
    loc = UserInputNode("loc_grace", GeoPoint)
    loc.push(GeoPoint(51.5, -0.1), "test")

    # A bus-only transit result with cost=0 (TfL returned no fare) and
    # no train/tube legs — the rail fare node must NOT make the commute
    # impossible.  It should return the original commute without the
    # fare, so the commute is still available on the property card.
    bus_only = _make_commute(duration_min=40, mode="bus", cost_gbp=0,
        details=(CostGroup(legs=(
            JourneyLeg(mode=LegMode.WALK, duration=Quantity(5, "minute")),
            JourneyLeg(mode=LegMode.BUS, duration=Quantity(20, "minute"), line_name="38"),
            JourneyLeg(mode=LegMode.WALK, duration=Quantity(5, "minute")),
        ), cost=Money("3.50", "GBP"), operator="TfL"),),
    )
    transit.push(bus_only, "test")

    node = RailFareNode("rf_grace", transit_result=transit, best_location=loc)
    await node.refresh(force=True)
    a = node.latest_attempt()
    # The key assertion: the commute is STILL AVAILABLE (not impossible)
    # even without the rail fare — the result is the original bus commute.
    assert a.succeeded, f"rail fare should succeed gracefully, got {a.status}: {a.error}"
    assert a.value_or_none() is not None
    assert a.value_or_none().duration.magnitude == 40