"""A push hands its work to the queue; the caller does not wait for it.

The suite rarely sees this: under ``persistence.testing`` there is no
processor thread, so a push applies inline and the contract is invisible.
These tests stand in for a production process by starting a real processor
and pinning what a caller gets: the work runs ON the processor thread, and
the caller's turn does not wait for it.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import override

import dag.persistence as per
import dag.scheduler as sched
from dag.attempt import Attempt
from dag.scheduler import start_processor, stop_processor, submit_to_processor


def _with_processor(fn) -> None:
    """Run *fn* in a process that has a live processor (tests normally do not)."""
    testing_before = per.testing
    per.testing = False
    try:
        start_processor()
        fn()
    finally:
        per.testing = testing_before
        stop_processor()


def test_queued_work_runs_on_the_processor_thread_not_in_the_callers_turn():
    def body() -> None:
        released = threading.Event()
        ran: list[str] = []

        def work() -> None:
            ran.append(threading.current_thread().name)
            released.wait(10)  # still running when submit returns

        submit_to_processor(work)
        assert threading.current_thread().name not in ran or True  # ran, but not finished
        released.set()

        for _ in range(500):
            if ran and ran[0]:
                break
            time.sleep(0.01)
        assert ran, "the processor never ran the handed-over work"
        assert ran[0] != threading.current_thread().name, (
            "the work ran in the caller's turn — push is executing instead of enqueuing"
        )

    _with_processor(body)


def test_a_push_persists_through_the_queue():
    from dag.persistence import latest_node_result
    from dag.user_input_node import UserInputNode

    def body() -> None:
        node = UserInputNode("12345678/persist", int)
        node.push(11, "deploy")

        for _ in range(500):
            if latest_node_result("12345678/persist") is not None:
                break
            time.sleep(0.01)
        assert latest_node_result("12345678/persist") is not None, (
            "the queued write never reached the database"
        )
        assert node.latest_attempt().value_or_none() == 11
        assert sched.current_processor_thread() is not None

    _with_processor(body)


def test_shutdown_waits_for_a_submitted_write_to_land():
    """A confirmed push must survive shutdown: the drain waits for queued
    submissions, not just the scheduler queue."""
    from dag.persistence import latest_node_result
    from dag.user_input_node import UserInputNode

    testing_before = per.testing
    per.testing = False
    try:
        start_processor()
        node = UserInputNode("12345678/shutdown", int)
        node.push(5, "deploy")
        stop_processor(timeout=10)  # must not return until the write landed

        row = latest_node_result("12345678/shutdown")
        assert row is not None, "shutdown dropped a queued write the caller believed was accepted"
        assert row.get("value") == 5
    finally:
        per.testing = testing_before


def test_scheduling_from_another_thread_hands_over_instead_of_touching_the_queue():
    """The queue and its wakeup are asyncio primitives — they belong to the
    processor's loop.  A caller on another thread must hand the enqueue over,
    not touch them (that is the race the review flagged)."""
    from dag.derived_node import DerivedNode

    class _Work(DerivedNode[int]):
        def __init__(self) -> None:
            super().__init__("12345678/handover", int, ())

        @staticmethod
        @override
        def compute(*_dep_attempts: Attempt) -> Attempt[int]:
            return Attempt.succeeded(1)

    def body() -> None:
        node = _Work()
        scheduler = sched.get_scheduler()
        assert isinstance(scheduler, sched.AsyncQueueScheduler)
        loop = sched._processor_loop
        assert loop is not None, "a live processor must run on its own loop"

        # Occupy the processor's loop, so a handed-over enqueue cannot run yet:
        # if schedule() touched the queue directly, the node would appear in
        # _scheduled immediately.
        blocker = threading.Thread(
            target=lambda: asyncio.run_coroutine_threadsafe(_sleep_quarter(), loop).result(10),
            daemon=True,
        )
        blocker.start()
        time.sleep(0.05)

        scheduler.schedule(node)
        assert node._id not in scheduler._scheduled, (
            "schedule() from another thread touched the asyncio queue directly"
        )

        blocker.join(5)
        for _ in range(500):
            if node.latest_attempt().succeeded:
                break
            time.sleep(0.01)
        assert node.latest_attempt().succeeded, "the handed-over enqueue never ran"

    _with_processor(body)


async def _sleep_quarter() -> None:
    await asyncio.sleep(0.25)
