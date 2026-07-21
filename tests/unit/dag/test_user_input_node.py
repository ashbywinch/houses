from __future__ import annotations

import pytest

from dag.persistence import latest_node_result
from dag.user_input_node import UserInputNode


class TestUserInputNode:
    @pytest.mark.asyncio
    async def test_initial_attempt_is_impossible(self):
        node = UserInputNode[int]("test", int)
        a = await node.attempt()
        assert a.succeeded is False

    @pytest.mark.asyncio
    async def test_push_makes_value_available(self):
        node = UserInputNode[int]("test", int)
        node.push(42, "test")
        a = await node.attempt()
        assert a.succeeded is True
        assert a.value_or_none() == 42

    @pytest.mark.asyncio
    async def test_push_overwrites_previous_value(self):
        node = UserInputNode[str]("test", str)
        node.push("first", "src1")
        node.push("second", "src2")
        a = await node.attempt()
        assert a.value_or_none() == "second"

    @pytest.mark.asyncio
    async def test_push_saves_provenance(self):
        node = UserInputNode[str]("test", str)
        node.push("hello", "Rightmove")
        p = await node.build_provenance()
        assert p.label == "Rightmove"

    def test_push_emits_changed_signal(self):
        received = []
        node = UserInputNode[str]("test", str)
        node.changed.connect(lambda: received.append("changed"))

        node.push("hello", "test")
        assert received == ["changed"]

    def test_multiple_pushes_emit_multiple_times(self):
        received = []
        node = UserInputNode[int]("test", int)
        node.changed.connect(lambda: received.append(1))

        node.push(1, "t")
        node.push(2, "t")
        assert received == [1, 1]

    @pytest.mark.asyncio
    async def test_to_json_after_push(self):
        node = UserInputNode[str]("test", str)
        node.push("hello", "src")
        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] == "hello"

    @pytest.mark.asyncio
    async def test_to_json_before_push(self):
        node = UserInputNode[int]("test", int)
        j = await node.to_json()
        assert j["status"] == "pending"
        assert j["value"] is None

    def test_id(self):
        node = UserInputNode[str]("my_source", str)
        assert node._id == "my_source"

    @pytest.mark.asyncio
    async def test_push_persists_to_db(self):
        node = UserInputNode[str]("persist_test", str)
        node.push("stored_value", "test")

        loaded = latest_node_result("persist_test")
        assert loaded is not None
        assert loaded["status"] == "succeeded"
        assert loaded["value"] == "stored_value"

    @pytest.mark.asyncio
    async def test_loads_from_db_on_init(self):
        node1 = UserInputNode[str]("db_reload", str)
        node1.push("from_db", "test")
        node_id = node1._id

        node2 = UserInputNode[str](node_id, str)
        a = await node2.attempt()
        assert a.succeeded is True
        assert a.value_or_none() == "from_db"
