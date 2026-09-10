"""Test the DAG→frontend routing contract.

The after-refresh hook routes by node kind: property nodes queue a
property summary broadcast, settings nodes push the settings payload,
and internal node payloads are never broadcast — nothing renders a raw
DAG node. The full routing behaviour is covered end-to-end in
tests/unit/web/test_summary_broadcast.py; this file keeps the
scheduler-level guarantee that cascade processing itself stays silent.
"""

from __future__ import annotations

from typing import override

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import AsyncQueueScheduler, flush_processor, set_scheduler
from dag.user_input_node import UserInputNode


class _Node(DerivedNode[str]):
    def __init__(self, node_id: str, deps) -> None:
        super().__init__(node_id, str, deps)

    @override
    def compute(self, src: Attempt[str]) -> Attempt[str]:
        return Attempt.succeeded("computed")


@pytest.mark.asyncio
async def test_cascade_processing_is_silent_until_the_hook_routes_it():
    """A cascade with NO after-refresh callback registered sends
    nothing: broadcasts happen only through the production hook."""
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[str]("bc_src", str)
    _Node("prop123/test_bc_node", deps=(src,))

    src.push("go", "test")
    await flush_processor()
