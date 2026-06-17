from __future__ import annotations

from dag.attempt import Provenance
from dag.source_node import SourceNode


class TestSourceNode:
    def test_initial_attempt_is_impossible(self):
        node = SourceNode[int]("test", int)
        a = node.attempt()
        assert a.is_succeeded is False

    def test_push_makes_value_available(self):
        node = SourceNode[int]("test", int)
        node.push(42, Provenance("test"))
        a = node.attempt()
        assert a.is_succeeded is True
        assert a.value_or_none() == 42

    def test_push_overwrites_previous_value(self):
        node = SourceNode[str]("test", str)
        node.push("first", Provenance("src1"))
        node.push("second", Provenance("src2"))
        a = node.attempt()
        assert a.value_or_none() == "second"

    def test_push_saves_provenance(self):
        node = SourceNode[str]("test", str)
        prov = Provenance("Rightmove", source_attempts={})
        node.push("hello", prov)
        a = node.attempt()
        assert a.provenance.label == "Rightmove"

    def test_push_emits_changed_signal(self):
        received = []
        node = SourceNode[str]("test", str)
        node.changed.connect(lambda: received.append("changed"))

        node.push("hello", Provenance("test"))
        assert received == ["changed"]

    def test_multiple_pushes_emit_multiple_times(self):
        received = []
        node = SourceNode[int]("test", int)
        node.changed.connect(lambda: received.append(1))

        node.push(1, Provenance("t"))
        node.push(2, Provenance("t"))
        assert received == [1, 1]

    def test_to_json_after_push(self):
        node = SourceNode[str]("test", str)
        node.push("hello", Provenance("src"))
        j = node.to_json()
        assert j["succeeded"] is True
        assert j["value"] == "hello"

    def test_to_json_before_push(self):
        node = SourceNode[int]("test", int)
        j = node.to_json()
        assert j["succeeded"] is False
        assert j["value"] is None

    def test_id(self):
        node = SourceNode[str]("my_source", str)
        assert node.id == "my_source"
