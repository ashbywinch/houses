"""Tests for Commute using Money type for daily_cost."""

from money import Money
from pint import Quantity

from houses.commute import LegMode
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.sheets.row import Row


def test_daily_cost_money_type():
    """Commute.daily_cost should be Money when constructed with Money."""
    c = Commute(
        person=Person(name="", has_car=False),
        label="",
        destination=PlaceOfInterest(label="", address=""),
        duration=Quantity(0, "minute"),
        daily_cost=Money("15.0", "GBP"),
        mode="transit",
    )
    assert isinstance(c.daily_cost, Money)


def test_cost_groups_tfl_cost_sum():
    """CostGroup with TfL operator should not be counted in non-rail cost by Row."""
    from houses.commute import CostGroup, JourneyLeg

    commute = Commute(
        person=Person(name="Simon", has_car=False),
        label="Office",
        destination=PlaceOfInterest(label="Office", address="SW1A 1AA"),
        duration=Quantity(45, "minute"),
        daily_cost=Money("15.00", "GBP"),
        mode="transit",
        details=(
            CostGroup(
                legs=(JourneyLeg(mode=LegMode.TRAIN, duration_minutes=45),),
                operator="TfL",
                cost=Money("15.00", "GBP"),
            ),
        ),
    )
    non_rail = Row._calc_non_rail_cost(commute)
    assert non_rail == 0.0
