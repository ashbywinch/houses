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


class TestUserInputNodeFail:
    @pytest.mark.asyncio
    async def test_fail_records_impossible_attempt(self):
        from money import Money

        node = UserInputNode[Money]("test", Money)
        node.fail("price value 'ask the agent' is not parseable", error_info=None)
        a = await node.attempt()
        assert a.impossible is True
        assert a.error_info is not None
        assert "ask the agent" in a.error_info.display_message

    @pytest.mark.asyncio
    async def test_fail_persists_impossible_row_and_reloads(self):
        node1 = UserInputNode[str]("fail_reload", str)
        node1.fail("structure changed")
        node_id = node1._id
        loaded = latest_node_result(node_id)
        assert loaded is not None and loaded["status"] == "impossible"

        node2 = UserInputNode[str](node_id, str)
        a = await node2.attempt()
        assert a.impossible is True
        assert loaded["error_detail"]["message"] == "structure changed"

    @pytest.mark.asyncio
    async def test_push_supersedes_fail(self):
        """A real value wins over a recorded failure."""
        node = UserInputNode[int]("test", int)
        node.fail("could not parse")
        node.push(42, "test")
        a = await node.attempt()
        assert a.succeeded is True
        assert a.value_or_none() == 42

    @pytest.mark.asyncio
    async def test_wire_reports_impossible_with_error(self):
        from money import Money

        node = UserInputNode[Money]("test", Money)
        node.fail("price value 'bogus' is not parseable")
        wire = await node.to_json_value()
        assert wire["status"] == "impossible"
        assert wire["value"] is None
        assert "bogus" in wire["error"]
