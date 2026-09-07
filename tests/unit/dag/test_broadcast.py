"""Test that node refreshes do NOT broadcast per-node updates.

User-visible contract: during initial DAG processing, the server must NOT
send WebSocket messages for every node refresh.  Broadcasts are only for
property-level events (add/delete) triggered via ``push_rid``.
"""

from __future__ import annotations

from typing import override
from unittest.mock import patch

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
async def test_after_refresh_does_not_broadcast():
    """The ``_after_refresh`` callback must NOT send any WebSocket
    messages.  It must be a no-op during cascade processing.
    """
    set_scheduler(AsyncQueueScheduler(respect_time=False))

    src = UserInputNode[str]("bc_src", str)
    node = _Node("prop123/test_bc_node", deps=(src,))

    src.push("go", "test")
    await flush_processor()

    import houses.web.broadcaster as bcast

    with patch.object(bcast, "_push_node_update") as mock_push_node:
        # The _after_refresh callback after processing should do nothing
        sched = _get_async_queue_scheduler()
        sched.after_refresh(node)

        assert not mock_push_node.called, (
            "_push_node_update should NOT be called during cascade. "
            "Node-level broadcasts are for user-triggered changes only."
        )


def _get_async_queue_scheduler():
    from dag.scheduler import AsyncQueueScheduler as _AsyncQS
    from dag.scheduler import get_scheduler

    s = get_scheduler()
    assert isinstance(s, _AsyncQS)
    return s
