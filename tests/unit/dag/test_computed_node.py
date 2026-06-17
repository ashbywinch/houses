from __future__ import annotations

from dag.attempt import Attempt, Provenance
from dag.computed_node import ComputedNode
from dag.source_node import SourceNode


class TestComputedNode:
    def test_recomputes_on_dep_change(self):
        src = SourceNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        src.push(2, Provenance("test"))
        assert node.attempt().value_or_none() == 4

        src.push(3, Provenance("test"))
        assert node.attempt().value_or_none() == 6

    def test_initial_attempt_runs_compute(self):
        src = SourceNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        # Not yet resolved
        a = node.attempt()
        assert a.is_succeeded is False

        src.push(5, Provenance("test"))
        a = node.attempt()
        assert a.value_or_none() == 10

    def test_caches_result_until_dep_changes(self):
        src = SourceNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        src.push(10, Provenance("test"))
        first = node.attempt()
        assert first.value_or_none() == 20

        # No push — cache hit
        second = node.attempt()
        assert second.value_or_none() == 20
        assert node.compute_count == 1  # computed on first attempt, cached on second

    def test_multiple_deps(self):
        a = SourceNode[int]("a", int)
        b = SourceNode[int]("b", int)
        node = _SumNode("sum", deps=(a, b))

        a.push(3, Provenance("t"))
        b.push(4, Provenance("t"))
        assert node.attempt().value_or_none() == 7

        a.push(10, Provenance("t"))
        assert node.attempt().value_or_none() == 14

    def test_changed_signal_fires_on_recompute(self):
        src = SourceNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        received = []
        node.changed.connect(lambda: received.append("changed"))

        src.push(2, Provenance("test"))
        assert received == ["changed"]

        src.push(3, Provenance("test"))
        assert received == ["changed", "changed"]

    def test_dep_failure_propagates(self):
        src = SourceNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        # No value pushed to src → src.attempt() returns impossible
        a = node.attempt()
        assert a.is_succeeded is False

    def test_to_json(self):
        src = SourceNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        src.push(4, Provenance("test"))
        j = node.to_json()
        assert j["value"] == 8


class _DoubleNode(ComputedNode[int]):
    """Doubles the input value."""

    def __init__(self, node_id: str, deps):
        super().__init__(node_id, int, deps)
        self.compute_count = 0

    def compute(self, *dep_attempts) -> Attempt[int]:
        self.compute_count += 1
        val = dep_attempts[0]
        if val.is_succeeded:
            return Attempt.succeeded(val.value_or_none() * 2,
                                     Provenance("doubled"))
        return Attempt.impossible("dep failed")


class _SumNode(ComputedNode[int]):
    """Sums two inputs."""

    def __init__(self, node_id: str, deps):
        super().__init__(node_id, int, deps)
        self.compute_count = 0

    def compute(self, *dep_attempts) -> Attempt[int]:
        self.compute_count += 1
        vals = [a.value_or_none() for a in dep_attempts]
        if all(a.is_succeeded for a in dep_attempts):
            return Attempt.succeeded(sum(vals), Provenance("sum"))
        return Attempt.impossible("one or more deps failed")
