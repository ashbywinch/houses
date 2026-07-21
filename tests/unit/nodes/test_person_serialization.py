"""Person push — deposit_equity must be dict or None, never bare number."""

import pytest


def test_int_deposit_equity_raises():
    """UserInputNode.push rejects Person with deposit_equity=177000 (int)."""
    from dag.user_input_node import UserInputNode
    from houses.model.domain import Person

    node = UserInputNode[list[Person]]("test", list[Person])  # type: ignore[valid-type]
    p = Person(name="Simon", has_car=True, deposit_equity=177000)
    with pytest.raises((ValueError, TypeError, AssertionError)):
        node.push([p], "test")


def test_float_deposit_equity_raises():
    """deposit_equity=177000.0 is also rejected."""
    from dag.user_input_node import UserInputNode
    from houses.model.domain import Person

    node = UserInputNode[list[Person]]("test_f", list[Person])  # type: ignore[valid-type]
    p = Person(name="Simon", has_car=True, deposit_equity=177000.0)
    with pytest.raises((ValueError, TypeError)):
        node.push([p], "test")


def test_dict_deposit_equity_succeeds():
    """deposit_equity={'amount': 200000, 'currency': 'GBP'} must serialize.

    The PATCH endpoint converts this dict to Money() before creating
    Person, so it reaches push() as a proper Money object.
    """
    from money import Money

    from dag.user_input_node import UserInputNode
    from houses.model.domain import Person

    node = UserInputNode[list[Person]]("test_d", list[Person])  # type: ignore[valid-type]
    p = Person(name="Simon", has_car=True, deposit_equity=Money("200000", "GBP"))
    node.push([p], "test")
    assert node._value is not None


def test_none_deposit_equity_succeeds():
    """deposit_equity=None must work."""
    from dag.user_input_node import UserInputNode
    from houses.model.domain import Person

    node = UserInputNode[list[Person]]("test_n", list[Person])  # type: ignore[valid-type]
    p = Person(name="George", has_car=False, deposit_equity=None)
    node.push([p], "test")
    assert node._value is not None
