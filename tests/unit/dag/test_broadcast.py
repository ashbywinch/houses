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


class TestSaveWorkerReliability:
    """Save-queue contract: errors never kill the worker or leak unfinished
    tasks; flush drains deterministically; reads see queued writes."""

    def test_flush_processes_enqueued_write(self):
        """flush_pending_saves() writes every queued item before returning."""
        import dag.persistence as per

        per.enqueue_save("flush_test/x", {"status": "succeeded", "value": 1}, None, "2026-01-01T00:00:00+00:00", None)
        per.flush_pending_saves()  # reads never flush; the test drains
        row = per.latest_node_result("flush_test/x")
        assert row is not None and row["value"] == 1

    def test_save_one_survives_failure(self, caplog):
        """A failing save is logged and never raised, task_done still runs
        (no queue hang), and the worker/flush keeps consuming.

        Per the plan, tests never start the worker thread: _save_one is
        the shared per-item code path for worker AND flush, so it is
        tested directly on the test thread — deterministic.
        """
        import logging

        import dag.persistence as per
        from unittest.mock import patch

        real = per.save_node_result
        calls = {"n": 0}

        def flaky(node_id, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("disk full")
            return real(node_id, *a, **k)

        per.flush_pending_saves()  # drain bootstrap/fixture leftovers
        per._save_queue.put(("flaky/a", {"v": 1}, None, "2026-01-01T00:00:01+00:00", None))
        per._save_queue.put(("flaky/b", {"v": 2}, None, "2026-01-01T00:00:02+00:00", None))
        with caplog.at_level(logging.ERROR):
            with patch.object(per, "save_node_result", flaky):
                per._save_one(per._save_queue.get())
                per._save_one(per._save_queue.get())

        assert calls["n"] == 2, "processing continues after an error"
        assert any("flaky/a" in r.getMessage() for r in caplog.records), "failure must be logged"
        assert per._save_queue.unfinished_tasks == 0, "task_done must run on error"
        assert per.latest_node_result("flaky/b") is not None, "later item still persisted"
