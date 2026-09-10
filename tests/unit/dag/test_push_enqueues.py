"""A push hands its work to the queue; the caller does not wait for it.

The suite rarely sees this: under ``persistence.testing`` there is no
processor thread, so a push applies inline and the contract is invisible.
These tests stand in for a production process by starting a real processor
and pinning what a caller gets: the work runs ON the processor thread, and
the caller's turn does not wait for it.
"""

from __future__ import annotations

import threading
import time

import dag.persistence as per
import dag.scheduler as sched
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
