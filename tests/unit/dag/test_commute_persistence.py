"""Test that previously computed commute values survive node re-creation.

User-visible contract: when the server restarts, all previously computed
commutes must appear as ``succeeded`` (not ``pending``) because they are
loaded from the persisted DAG node result cache.
"""

from __future__ import annotations

from typing import override

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.model.domain import Commute, Person, PlaceOfInterest


@pytest.mark.asyncio
async def test_commute_survives_node_recreation():
    """A Commute-typed DerivedNode loads its persisted value from DB
    when re-created with the same node ID — and reports ``succeeded``,
    not ``pending``."""
    # ── Phase 1: compute and persist a Commute value ──────────────────
    src = UserInputNode[str]("csnrs_src", str)
    node = _CommutePassthroughNode("csnrs_result", deps=(src,))

    src.push("go", "test")
    await flush_processor()
    await flush_processor()

    j1 = await node.to_json()
    assert j1["status"] == "succeeded", f"Phase 1 should succeed, got {j1['status']}"

    # ── Phase 2: re-create node (simulate server restart) ─────────────
    src2 = UserInputNode[str]("csnrs_src", str)
    node2 = _CommutePassthroughNode("csnrs_result", deps=(src2,))

    j2 = await node2.to_json()
    assert j2["status"] == "succeeded", (
        f"Phase 2 (re-created node) should load succeeded from DB, "
        f"got status={j2['status']!r}. "
        f"If 'pending', the persisted result was not loaded — "
        f"commutes will appear missing on server restart."
    )


class _CommutePassthroughNode(DerivedNode[Commute]):
    """Returns a fixed Commute value once its dep is ready."""

    def __init__(self, node_id: str, deps) -> None:
        super().__init__(node_id, Commute, deps)
        self._called = 0

    @override
    def compute(self, src: Attempt[str]) -> Attempt[Commute]:
        self._called += 1
        return Attempt.succeeded(
            Commute(
                person=Person(name="Test", has_car=False),
                label="Office",
                destination=PlaceOfInterest(label="Office", address="SW1V 2QQ"),
                duration=Quantity(45, "minute"),
                daily_cost=Money("7.20", "GBP"),
                mode="transit",
                _details=(),
                is_child=False,
            )
        )
