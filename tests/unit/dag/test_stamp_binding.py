"""A consumer must record the freshness stamp of the dep ATTEMPT it bound,
never a stamp read after its compute yielded.

The group-monthly-cost regression: a dep (monthly_mortgage) refreshed
between the consumer's attempt snapshot and its persist; the consumer
recorded the dep's NEW row stamp with the OLD attempt's value. Stamps and
value then agree with nothing, and the consumer is never marked stale
again — it serves the wrong figure forever.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import override

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.persistence import latest_node_result
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode


class _GatedDoubler(DerivedNode[int]):
    """Doubles its input; compute waits on a caller-provided gate so the
    test can simulate a concurrent dep row rewrite mid-compute."""

    def __init__(self, node_id, *, src):
        super().__init__(node_id, int, (src,))
        self.gate: asyncio.Event | None = None
        self.started: asyncio.Event = asyncio.Event()

    @override
    async def compute(self, src: Attempt) -> Attempt:
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return Attempt.succeeded(2 * (src.value_or_none() or 0))


@pytest.mark.asyncio
async def test_consumer_records_the_captured_stamp_not_a_mid_flight_rewrite():
    run = uuid.uuid4().hex[:8]
    src = UserInputNode(f"sb_src_{run}", int)
    node = _GatedDoubler(f"sb_dbl_{run}", src=src)
    await flush_processor()
    src.push(1, "test")
    await flush_processor()
    assert node.latest_attempt().value_or_none() == 2
    stamp_used = src._db_created_at
    assert stamp_used is not None

    # Begin the consumer's refresh; its compute suspends AFTER the dep
    # attempt (and its stamp) are captured.
    node.gate = asyncio.Event()
    node.started.clear()
    task = asyncio.create_task(node.refresh(force=True))
    await node.started.wait()

    # The dep row is REWRITTEN mid-compute — exactly what a concurrent
    # refresh of the dep does (new row, new created_at) — while the
    # consumer's bound attempt is still the old one.
    src._db_created_at = "2099-01-01T00:00:00+00:00"
    node.gate.set()
    await task

    # The persisted row must carry the stamp OF THE ATTEMPT THE COMPUTE
    # READ — not the stamp read after compute yielded. Recording the new
    # stamp makes the consumer look fresh forever while serving the old
    # value (the 2026-09-18 group-monthly-cost regression).
    row = latest_node_result(node._id)
    assert row is not None
    assert row["_dep_timestamps"][src._id] == stamp_used, (
        f"stamp_used={stamp_used} row_stamp={row['_dep_timestamps'].get(src._id)} "
        f"value={row['value']} — the persisted dep stamp must match the attempt "
        "the compute bound, not a stamp read after compute yielded"
    )
    assert row["value"] == 2, "the refresh must bind the pre-rewrite attempt"


class _DictValued(DerivedNode[dict]):
    """A dep whose value is a machine dict — parent trees must state it
    humanly, never embed the raw dump."""

    def __init__(self, node_id, src):
        super().__init__(node_id, dict, (src,))

    @override
    def provenance_display_value(self, att) -> str:
        return "£9,166.88/yr"

    @override
    async def compute(self, src: Attempt) -> Attempt:
        return Attempt.succeeded({"persons": {"Simon": {"daily_gbp": "13.86"}}, "yearly_total_gbp": "9166.88"})


@pytest.mark.asyncio
async def test_parent_tree_states_a_dict_deps_value_humanly():
    src = UserInputNode("dv_src", int)
    dep = _DictValued("dv_dict", src)
    await flush_processor()
    src.push(1, "test")
    await flush_processor()
    att = dep.latest_attempt()

    sub = await DerivedNode._dep_recorded_subtree(dep, att)
    assert sub.value == "£9,166.88/yr", "a dict dep must be stated humanly in parent trees"
    assert "daily_gbp" not in json.dumps(sub.to_dict())
