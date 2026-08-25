"""Test that Commute's daily_cost is always Money, never a raw number.

User-visible contract: a commute with a numeric daily_cost must correctly
display its cost in the frontend.  If daily_cost silently becomes a float,
``to_json()`` crashes with ``'float' object has no attribute 'amount'``
and the node is persisted as ``impossible`` — the frontend shows ``?``.
"""

from __future__ import annotations

from typing import override

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import AsyncQueueScheduler, flush_processor, set_scheduler
from dag.user_input_node import UserInputNode
from houses.model.domain import Commute, Person, PlaceOfInterest

DURATION = Quantity(45, "minute")


class _FloatCostNode(DerivedNode[Commute]):
    """Returns a Commute with a float daily_cost — simulates the bug."""

    def __init__(self, node_id: str, deps) -> None:
        super().__init__(node_id, Commute, deps)

    @override
    def compute(self, src: Attempt[str]) -> Attempt[Commute]:
        return Attempt.succeeded(
            Commute(
                person=Person(name="Test", has_car=False),
                label="Office",
                destination=PlaceOfInterest(label="Office", address="SW1V 2QQ"),
                duration=DURATION,
                daily_cost=7.2,  # type: ignore[arg-type]  # why: deliberately-wrong type is the fixture — this test asserts a raw float daily_cost raises at to_json() instead of persisting silently
                mode="transit",
            )
        )


@pytest.mark.asyncio
async def test_float_daily_cost_raises_at_to_json():
    """A Commute with float daily_cost must raise at serialization time,
    NOT silently persist as impossible."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[str]("fdc_src3", str)
    node = _FloatCostNode("fdc_result3", deps=(src,))

    src.push("go", "test")
    await flush_processor()

    with pytest.raises((TypeError, AttributeError, ValueError)):
        await node.to_json()


class _ProperNode(DerivedNode[Commute]):
    """Returns a Commute with proper Money daily_cost — control."""

    def __init__(self, node_id: str, deps) -> None:
        super().__init__(node_id, Commute, deps)

    @override
    def compute(self, src: Attempt[str]) -> Attempt[Commute]:
        return Attempt.succeeded(
            Commute(
                person=Person(name="Test", has_car=False),
                label="Office",
                destination=PlaceOfInterest(label="Office", address="SW1V 2QQ"),
                duration=DURATION,
                daily_cost=Money("7.20", "GBP"),
                mode="transit",
            )
        )


@pytest.mark.asyncio
async def test_money_daily_cost_serialises_ok():
    """A Commute with proper Money daily_cost serialises correctly."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[str]("pdc_src2", str)
    node = _ProperNode("pdc_result2", deps=(src,))

    src.push("go", "test")
    await flush_processor()

    j = await node.to_json()
    assert j["status"] == "succeeded"
    cost = j.get("value", {}).get("daily_cost", {})
    assert cost.get("amount") == "7.20"
    assert cost.get("currency") == "GBP"
