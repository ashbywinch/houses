"""Tests for Commute using Money type for daily_cost_gbp."""

from money import Money

from houses.commute import Commute
from houses.sheets.row import Row


def test_daily_cost_money_type():
    """Commute.daily_cost_gbp should be Money when constructed with Money."""
    c = Commute(destination_label="", destination_postcode="", daily_cost_gbp=Money("15.0", "GBP"))
    assert isinstance(c.daily_cost_gbp, Money)


def test_daily_cost_none():
    """Commute.daily_cost_gbp defaults to None."""
    c = Commute(destination_label="", destination_postcode="")
    assert c.daily_cost_gbp is None


def test_money_arithmetic():
    """Money supports addition and multiplication by int."""
    result = (Money("10.0", "GBP") + Money("5.0", "GBP")) * 2
    assert result == Money("30.0", "GBP")


def test_money_comparison():
    """Money equality is exact."""
    assert Money("10.0", "GBP") == Money("10.0", "GBP")
    assert Money("10.0", "GBP") != Money("10.01", "GBP")


def test_fmt_cost_with_money():
    """_fmt_cost passes Money str() to the sheet (currency + amount)."""
    assert Row._fmt_cost(Money("8.50", "GBP")) == "8.50"


def test_fmt_cost_with_none():
    """_fmt_cost returns empty string for None."""
    assert Row._fmt_cost(None) == ""
