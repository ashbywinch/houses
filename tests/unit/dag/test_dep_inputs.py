"""Failing first: refresh stores the exact dep inputs; provenance reads them back."""

from __future__ import annotations

from typing import override

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import DepInputs
from dag.user_input_node import UserInputNode
from tests.unit.conftest import flush_all


class _Sum(DerivedNode[int]):
    def __init__(self, node_id, *, a, b):
        super().__init__(node_id, int, (a, b))

    @override
    def compute(self, a: Attempt[int], b: Attempt[int]) -> Attempt[int]:
        if not a.succeeded:
            return a
        if not b.succeeded:
            return b
        return Attempt.succeeded((a.value_or_none() or 0) + (b.value_or_none() or 0))


def test_refresh_stores_projected_dep_inputs():
    a = UserInputNode("di_a", int)
    b = UserInputNode("di_b", int)
    a.push(2, "test")
    b.push(3, "test")
    s = _Sum("di_sum", a=a, b=b)
    flush_all()
    assert s.latest_attempt().value_or_none() == 5
    stored = s._stored_dep_inputs()
    assert isinstance(stored, DepInputs)
    assert stored.inputs == {"di_a": {"display": 2, "value": 2}, "di_b": {"display": 3, "value": 3}}, stored.inputs


def test_stored_inputs_survive_dep_moving_on():
    a = UserInputNode("di2_a", int)
    b = UserInputNode("di2_b", int)
    a.push(2, "test")
    b.push(3, "test")
    s = _Sum("di2_sum", a=a, b=b)
    flush_all()
    assert s.latest_attempt().value_or_none() == 5
    # The deps move on WITHOUT this node refreshing (no signal path in
    # this bare test) — the stored inputs must still be the old ones.
    a.push(20, "test")
    stored = s._stored_dep_inputs()
    assert stored.inputs == {"di2_a": {"display": 2, "value": 2}, "di2_b": {"display": 3, "value": 3}}, stored.inputs


def test_failed_dep_stores_status_and_error():
    a = UserInputNode("di3_a", int)
    b = UserInputNode("di3_b", int)
    a.push(2, "test")
    # b never pushed: refresh records the dep failure, not a re-derivation.
    s = _Sum("di3_sum", a=a, b=b)
    flush_all()
    stored = s._stored_dep_inputs()
    assert stored.inputs.get("di3_a", {}).get("display") == 2
    assert stored.inputs.get("di3_b", {}).get("status") == "pending", stored.inputs
