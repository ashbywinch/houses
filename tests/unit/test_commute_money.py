"""Tests for Commute using Money type for daily_cost."""

from money import Money
from pint import Quantity

from houses.model.domain import Commute, Person, PlaceOfInterest


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

