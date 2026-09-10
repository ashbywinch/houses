"""The graph walk must reach every node whose code or inputs moved on —
including nodes a conditional dependency set hides.

A node may legitimately evaluate one alternative at a time (an if-then-else
branch).  That narrowing answers "what does THIS evaluation need"; it must
never narrow a REFRESH walk, or the nodes in the other branch are never
revisited after a deploy and keep their stale persisted results for good.

Live 2026-09-10: the walk followed the narrowed set, so a failed pipeline
behind a wrapper was invisible to the code-change sweep and stayed dead —
and the same blindness hit the start-up reconciliation.
"""

from __future__ import annotations

from typing import override

from dag.attempt import Attempt, AttemptError
from dag.derived_node import DerivedNode
from dag.node import Node
from dag.regenerate import schedule_code_stale_nodes


class _Pipeline(DerivedNode[int]):
    """A pipeline node, like one step of a commute chain."""

    def __init__(self, node_id: str) -> None:
        super().__init__(node_id, int, ())

    @staticmethod
    @override
    def compute(*_dep_attempts: Attempt) -> Attempt[int]:
        return Attempt.succeeded(1)


class _ConditionalBranch(DerivedNode[object]):
    """A node that evaluates one dependency at a time — if-then-else's shape:
    its ACTIVE deps are the chosen branch, its structural deps are both."""

    def __init__(self, node_id: str, *, chosen: _Pipeline, other: _Pipeline) -> None:
        self._chosen: _Pipeline = chosen
        self._other: _Pipeline = other
        super().__init__(node_id, object, (chosen, other))

    @staticmethod
    @override
    def compute(*_dep_attempts: Attempt) -> Attempt[object]:
        return Attempt.succeeded(1)

    @override
    def _get_active_deps(self) -> tuple[Node, ...]:
        return (self._chosen,)


def test_a_node_in_the_unchosen_branch_is_still_refreshed() -> None:
    unchosen = _Pipeline("x/unchosen")
    unchosen._persisted_code_version = "fingerprint-of-older-code"
    chosen = _Pipeline("x/chosen")
    chosen._persisted_code_version = "fingerprint-of-older-code"
    branch = _ConditionalBranch("x/branch", chosen=chosen, other=unchosen)
    branch._attempt = Attempt.succeeded(1)

    assert branch._get_active_deps() == (chosen,), "the branch evaluates only one alternative"
    scheduled = schedule_code_stale_nodes([branch])

    assert {n._id for n in scheduled} == {"x/chosen", "x/unchosen"}, (
        "the walk followed the active set and never reached the unchosen branch — "
        "its stale nodes would keep their old results forever"
    )


def test_the_walk_still_schedules_the_whole_reachable_graph() -> None:
    pipeline = _Pipeline("x/pipeline")
    pipeline._persisted_code_version = "fingerprint-of-older-code"
    branch = _ConditionalBranch("x/branch", chosen=pipeline, other=pipeline)
    branch._persisted_code_version = "fingerprint-of-older-code"

    scheduled = schedule_code_stale_nodes([branch])

    assert {n._id for n in scheduled} == {"x/branch", "x/pipeline"}


def test_sweep_refreshes_a_node_whose_loaded_deps_moved_on() -> None:
    """A process start can race a dependency's write: this node loads an
    older row than its dependency's newest row, and no signal ever fires for
    a write that happened before both loaded.  Start-up reconciliation is
    the only thing that catches it (live 2026-09-10: a commute served no
    value for the whole session although its pipeline had a priced journey
    on disk)."""
    pipeline = _Pipeline("x/pipeline")
    pipeline._attempt = Attempt.succeeded(1)
    pipeline._db_created_at = "2026-09-10T10:55:55+00:00"
    branch = _ConditionalBranch("x/branch", chosen=pipeline, other=pipeline)
    branch._attempt = Attempt.succeeded(1)
    branch._loaded_dep_timestamps = {"x/pipeline": "2026-09-10T09:00:00+00:00"}

    scheduled = schedule_code_stale_nodes([branch])

    assert any(n._id == "x/branch" for n in scheduled), (
        "the node disagrees with the pipeline row on disk and is never refetched"
    )


def test_a_domain_impossible_is_a_result_and_is_not_retried() -> None:
    """'No route to this destination' is an answer, not a failure: it must
    not be recomputed on every start (it costs a planner call and returns
    the same thing)."""
    pipeline = _Pipeline("x/pipeline")
    pipeline._attempt = Attempt.impossible(
        "no route",
        error_info=AttemptError(code="no_data", message="no route", retryable=False),
    )

    assert pipeline.needs_refresh() is False
    assert schedule_code_stale_nodes([pipeline]) == []
