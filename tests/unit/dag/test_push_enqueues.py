"""The push contract, pinned without threads.

A test that starts a real processor thread observes scheduling, not a
contract — and it brings a second thread to a connection the isolation
fixture built as one-thread-by-design.  The threading property is
correct by construction (docs/dag-library.md → 'Thread rules' →
'Tests'): ``submit_to_processor`` has two branches — no processor loop
(tests, startup, scripts) → apply the work inline; a live loop on the
processor thread → hand the work over with ``run_coroutine_threadsafe``
and count it until its callback lands.  ``stop_processor`` drains the
queue AND that count before joining.  Nothing in the suite needs to
believe a thread exists for any of that to hold.

What is pinned here: a push validates, replaces the value in the
caller's turn (read-your-own-write), and reaches persistence through
exactly that one handoff — plus the convention that keeps the suite
threadless.
"""

from __future__ import annotations

import dag.scheduler as sched
from dag.user_input_node import UserInputNode


def test_a_push_replaces_the_value_now_and_persists_through_the_seam():
    """push() → submit_to_processor is the ONLY path to the row (thread
    rules rule 3: persistence is a pipeline step, nothing else writes)."""
    from dag.persistence import latest_node_result

    node = UserInputNode("12345678/persist", int)
    node.push(11, "deploy")

    # Read-your-own-write: the new value is visible the moment push returns.
    assert node.latest_attempt().value_or_none() == 11

    row = latest_node_result("12345678/persist")
    assert row is not None and row.get("value") == 11
    assert row.get("source_label") == "deploy"


def test_a_push_validates_before_replacing_the_value():
    """push() validates the payload before touching the stored value — a
    malformed value must never replace (or half-replace) the current one."""
    from dag.persistence import latest_node_result

    node = UserInputNode("12345678/validate", int)
    node.push(11, "deploy")
    try:
        node.push("not-an-int", "deploy")  # type: ignore[arg-type]  # why: a deliberately-wrong payload is the fixture — push must reject it before touching the stored value
    except Exception:
        pass
    else:
        raise AssertionError("push() accepted a non-int into an int-typed node")

    assert node.latest_attempt().value_or_none() == 11, "the invalid push clobbered the good value"
    row = latest_node_result("12345678/validate")
    assert row is not None and row.get("value") == 11


def test_queued_work_is_synchronous_without_a_processor_thread():
    """The suite-stays-deterministic convention: with no processor loop —
    tests, startup, scripts — queued work applies in the caller's turn.
    A real thread must never be required for the pipeline to behave; the
    isolation fixture asserts none was started."""
    ran: list[str] = []
    sched.submit_to_processor(lambda: ran.append("done"))
    assert ran == ["done"]
    assert sched.current_processor_thread() is None
