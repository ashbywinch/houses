from __future__ import annotations

from dataclasses import dataclass

import pytest

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
        assert not result.succeeded
        assert "test_node" in result.error
        assert "dep_a: HTTP 503" in result.error
        assert "dep_b: not set" in result.error
        assert "cache expired" in result.error

    def test_impossible_without_extra(self):
        node = _ConcreteNode("test_node", str)

        dep_attempts = {
            "origin": Attempt.impossible("no address"),
        }
        result = node.call_impossible(dep_attempts)
        assert "origin: no address" in result.error
        assert result.error.startswith("test_node")

    def test_impossible_with_succeeded_deps(self):
        node = _ConcreteNode("test_node", str)

        dep_attempts = {
            "ok": Attempt.succeeded("value"),
            "fail": Attempt.impossible("broken"),
        }
        result = node.call_impossible(dep_attempts)
        assert "ok:" not in result.error
        assert "fail: broken" in result.error

    @pytest.mark.asyncio
    async def test_to_json_with_succeeded(self):
        node = _ConcreteNode("test_node", str)
        node._test_attempt = Attempt.succeeded("hello")

        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] == "hello"
        assert "error" not in j
        assert j["provenance"]["label"] == "test_node"

    @pytest.mark.asyncio
    async def test_to_json_with_impossible(self):
        node = _ConcreteNode("test_node", str)
        node._test_attempt = Attempt.impossible("something failed")

        j = await node.to_json()
        assert j["status"] == "impossible"
        assert j["value"] is None
        assert j["error"] == "something failed"

    @pytest.mark.asyncio
    async def test_to_json_with_complex_type(self):
        @dataclass
        class Point:
            x: int
            y: int

        node = _ConcreteNode("point_node", Point)
        node._test_attempt = Attempt.succeeded(Point(x=1, y=2))

        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] == {"x": 1, "y": 2}

    def test_changed_signal(self):
        received = []
        node = _ConcreteNode("test_node", str)
        node.changed.connect(lambda: received.append("changed"))

        node.emit_for_test()
        assert received == ["changed"]

    def test_id_property(self):
        node = _ConcreteNode("my_id", int)
        assert node._id == "my_id"

    @pytest.mark.asyncio
    async def test_attempt_async(self):
        node = _ConcreteNode("test", str)
        node._test_attempt = Attempt.succeeded("val")
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "val"

    @pytest.mark.asyncio
    async def test_provenance_description_in_json(self):
        node = _ConcreteNode("desc_test", str)
        node._test_attempt = Attempt.succeeded("val")
        node._test_provenance = Provenance("test", description="TfL transit route")
        j = await node.to_json()
        assert j["provenance"]["description"] == "TfL transit route"


class _ConcreteNode(Node[str]):
    """Concrete subclass for testing the abstract Node base."""

    def __init__(self, node_id: str, value_type: type):
        super().__init__(node_id, value_type)
        self._test_attempt = Attempt.impossible("not set")
        self._test_provenance = Provenance(label=node_id)

    async def attempt(self) -> Attempt:
        return self._test_attempt

    async def build_provenance(self) -> Provenance:
        return self._test_provenance

    def call_impossible(self, dep_attempts, extra=""):
        return self._impossible(dep_attempts, extra)

    def emit_for_test(self):
        self.changed.emit()
