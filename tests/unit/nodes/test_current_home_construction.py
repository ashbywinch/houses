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


@pytest.mark.asyncio
async def test_a_late_reprice_of_the_current_home_rederives_the_baseline(_sqlite_memory):
    """The winner's cost and address nodes must be WIRED as signal edges, not
    merely returned as active deps.

    2026-10-05: the baseline had been derived before its property's chain
    settled, and nothing re-queued it — ``_deps`` (the signal graph) held
    only the status nodes, so a re-price of the current home wrote without
    signalling. The index then showed monthly TOTALS instead of the change
    vs your home for a whole session, even though the delta code was intact.
    """
    from houses.nodes.current_home_node import _reset

    _reset()
    status = UserInputNode[str]("p100/status", str)
    cost = UserInputNode[dict]("p100/group_monthly_cost", dict)
    address = UserInputNode[str]("p100/best_address", str)

    class _Prop:
        comment_status = status
        group_monthly_cost = cost
        best_address = address

    class _Registry:
        """The seam the node reads the winner's figures through (the real
        registry is typed PropertyNodes, but the wiring under test is this
        node's)."""

        def get(self, rid: str) -> _Prop | None:
            return _Prop() if rid == "p100" else None

    node = CurrentHomeNode()
    node.add_status(status, _Registry(), cost_node=cost, address_node=address)

    status.push("current", "test")
    await flush_processor()
    assert (await node.attempt()).value_or_none() is None, (
        "no figure yet — the baseline is legitimately empty"
    )

    # The current home's chain settles LATE (its own value was unavailable
    # when the baseline was first derived).
    cost.push(
        {"couple": {"value": "1873.81", "stddev": 0}, "others": {"value": "652.92", "stddev": 0}},
        "test",
    )
    address.push("31 Isambard Road, Southall", "test")
    await flush_processor()

    att = await node.attempt()
    baseline = att.value_or_none()
    assert baseline is not None, "a re-price of the current home must re-derive the baseline"
    assert baseline.rid == "p100"
    assert baseline.address == "31 Isambard Road, Southall"

def test_the_baseline_provenance_value_projects_to_a_tree():
    """The DAG's projector walks a projected value BY TYPE (dag/attempt.py):
    dict recurses; a record class must expose to_provenance_value() — a
    Mapping satisfies neither. This is the regression the smoke box found:
    settings/current_home went impossible with "value of type
    BaselineProvenanceValue has no provenance projection"."""
    from dag.attempt import project_value
    from houses.nodes.current_home_node import MonthlyBaseline

    baseline = MonthlyBaseline(
        rid="111",
        address="1 Test St",
        group_value={},
        others_rent_paid=0.0,
    )

    assert project_value(baseline.to_provenance_value()) == {
        "rid": "111",
        "address": "1 Test St",
        "couple": None,
        "others": None,
    }
