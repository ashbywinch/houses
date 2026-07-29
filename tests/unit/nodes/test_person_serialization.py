"""Person push — Money fields must accept dict or number."""

import pytest


def test_home_sale_price_int_raises():
    """UserInputNode rejects Person with home_sale_price=500000 (int)."""
    from dag.user_input_node import UserInputNode
    from houses.model.domain import Person

    node = UserInputNode[list[Person]]("test", list[Person])
    p = Person(name="Simon", has_car=True, home_sale_price=500000)
    with pytest.raises((ValueError, TypeError, AssertionError)):
        node.push([p], "test")


def test_home_sale_price_dict_succeeds():
    """home_sale_price as Money dict must serialize."""
    from money import Money

    from dag.user_input_node import UserInputNode
    from houses.model.domain import Person

    node = UserInputNode[list[Person]]("test_d", list[Person])
    p = Person(
        name="Simon", has_car=True,
        home_sale_price=Money("500000", "GBP"),
    )
    node.push([p], "test")
    assert node._value is not None


def test_default_values_succeed():
    """All default Money values must serialize."""
    from dag.user_input_node import UserInputNode
    from houses.model.domain import Person

    node = UserInputNode[list[Person]]("test_n", list[Person])
    p = Person(name="George", has_car=False)
    node.push([p], "test")
    assert node._value is not None


def test_cash_contribution_float_raises():
    """cash_contribution=300000.0 (float) is rejected."""
    from dag.user_input_node import UserInputNode
    from houses.model.domain import Person

    node = UserInputNode[list[Person]]("test_f", list[Person])
    p = Person(
        name="Ashby", has_car=True, cash_contribution=300000.0
    )
    with pytest.raises((ValueError, TypeError)):
        node.push([p], "test")
