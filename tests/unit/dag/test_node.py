from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import TypeAdapter

from dag.attempt import Attempt, Provenance
from dag.node import Node


class TestNodeBase:
    def test_impossible_composes_all_errors(self):
        node = _ConcreteNode("test_node", str)

        dep_attempts = {
            "dep_a": Attempt.impossible("HTTP 503"),
            "dep_b": Attempt.impossible("not set"),
        }
        result = node.call_impossible(dep_attempts, extra="cache expired")
        assert not result.is_succeeded
        assert "test_node" in result._error
        assert "dep_a: HTTP 503" in result._error
        assert "dep_b: not set" in result._error
        assert "cache expired" in result._error

    def test_impossible_without_extra(self):
        node = _ConcreteNode("test_node", str)

        dep_attempts = {
            "origin": Attempt.impossible("no address"),
        }
        result = node.call_impossible(dep_attempts)
        assert "origin: no address" in result._error
        assert result._error.startswith("test_node")

    def test_impossible_with_succeeded_deps(self):
        node = _ConcreteNode("test_node", str)

        dep_attempts = {
            "ok": Attempt.succeeded("value", Provenance("test")),
            "fail": Attempt.impossible("broken"),
        }
        result = node.call_impossible(dep_attempts)
        assert "ok:" not in result._error  # only failed deps appear
        assert "fail: broken" in result._error

    def test_to_json_with_succeeded(self):
        node = _ConcreteNode("test_node", str)
        node._test_attempt = Attempt.succeeded("hello", Provenance("test_src"))

        j = node.to_json()
        assert j["succeeded"] is True
        assert j["value"] == "hello"
        assert j["error"] is None
        assert j["provenance"]["label"] == "test_src"

    def test_to_json_with_impossible(self):
        node = _ConcreteNode("test_node", str)
        node._test_attempt = Attempt.impossible("something failed")

        j = node.to_json()
        assert j["succeeded"] is False
        assert j["value"] is None
        assert j["error"] == "something failed"

    def test_to_json_with_complex_type(self):
        @dataclass
        class Point:
            x: int
            y: int

        node = _ConcreteNode("point_node", Point)
        node._test_attempt = Attempt.succeeded(Point(x=1, y=2), Provenance("test"))

        j = node.to_json()
        assert j["succeeded"] is True
        assert j["value"] == {"x": 1, "y": 2}

    def test_changed_signal(self):
        received = []
        node = _ConcreteNode("test_node", str)
        node.changed.connect(lambda: received.append("changed"))

        node.emit_for_test()
        assert received == ["changed"]

    def test_id_property(self):
        node = _ConcreteNode("my_id", int)
        assert node.id == "my_id"


class _ConcreteNode(Node[str]):
    """Concrete subclass for testing the abstract Node base."""

    def __init__(self, node_id: str, value_type: type):
        super().__init__(node_id, value_type)
        self._test_attempt = Attempt.impossible("not set")

    def attempt(self) -> Attempt:
        return self._test_attempt

    def call_impossible(self, dep_attempts, extra=""):
        return self._impossible(dep_attempts, extra)

    def emit_for_test(self):
        self.changed.emit()
