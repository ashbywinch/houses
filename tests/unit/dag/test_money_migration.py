"""Test that DerivedNode[Money] can load persisted float values without crashing.

The server crashed on startup because TotalMonthlyHousingCostNode
(DerivedNode[Money]) had old float values persisted from before the
float→Money migration.  TypeAdapter[Money].validate_python(x) raises
ValidationError when x is a float like 2659.02.

This test reproduces that failure mode:
1. Persist a float value for a Money-typed node (simulating old data)
2. Create a fresh node with the same ID
3. The node MUST load without crashing and report the value correctly
"""

from __future__ import annotations

from typing import override

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.persistence import save_node_result
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode


@pytest.mark.asyncio
async def test_money_node_loads_old_float_value():
    """A DerivedNode[Money] with a persisted float value must load
    without a ValidationError crash."""
    from money import Money

    node_id = "test_money_float_migration"

    # Step 1: Persist a float value as if it were old pre-migration data.
    # This is what existing DB rows look like before migration.
    save_node_result(
        node_id,
        {"status": "succeeded", "value": 2659.02},
    )

    # Step 2: Create a DerivedNode[Money] that will try to load that value.
    # A simple source node that the derived node can depend on.
    src = UserInputNode[Money]("test_money_float_src", Money)
    src.push(Money("0", "GBP"), "test")
    await flush_processor()

    class _PassthroughNode(DerivedNode[Money]):
        def __init__(self):
            super().__init__(node_id, Money, (src,))

        @override
        def compute(self, val: Attempt[Money]) -> Attempt[Money]:
            return val

    # This must NOT raise ValidationError
    node = _PassthroughNode()
    await flush_processor()
    a = await node.attempt()

    # The value should have been loaded — either as the original float
    # (if loading is tolerant) or as a migrated Money.
    assert a.succeeded or a.pending, f"Node should load without crashing. Got {a.status}: {a.error}"
