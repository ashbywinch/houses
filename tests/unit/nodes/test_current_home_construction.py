"""Construction-order regression for CurrentHomeNode.

2026-10-03: ``DerivedNode.__init__`` loads a persisted attempt, and the
scheduler may call ``_is_stale()`` during ``register()`` — reaching
``_get_active_deps()`` → ``_current_property()`` — BEFORE
``CurrentHomeNode.__init__`` had assigned ``_registry``. On a box whose DB
holds a persisted (non-pending, matching code version) ``settings/current_home``
attempt — written by the convergence sweep after the previous boot — the app
crashed in a restart loop at startup ("'CurrentHomeNode' object has no
attribute '_registry'"). The attribute must exist before the base constructor
runs.
"""
import pytest

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.nodes.current_home_node import CurrentHomeNode


@pytest.mark.asyncio
async def test_current_home_constructs_when_a_persisted_attempt_exists(_sqlite_memory):
    # (The autouse _inject_test_scheduler fixture already installed an
    # isolated AsyncQueueScheduler(respect_time=False) for this test.)
    # Drive a REAL succeeded attempt to persistence, the way the convergence
    # sweep does (status dep → compute → flush).
    status = UserInputNode[str]("p100/status", str)
    node = CurrentHomeNode()
    node.add_status(status, None)
    status.push("current", "test")
    await flush_processor()
    assert (await node.attempt()).succeeded
    # Re-creation: a fresh node loads that non-pending, matching attempt, so
    # register() evaluates _is_stale() → _get_active_deps() during
    # construction — the exact production path that read the not-yet-assigned
    # _registry.
    reloaded = CurrentHomeNode()  # must not raise AttributeError
    assert reloaded._registry is None