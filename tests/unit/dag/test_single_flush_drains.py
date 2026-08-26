"""Regression: ONE flush_processor() call must drain the whole cascade.

coding-standards.md: a node's refresh queues its dependents inside the
same drain loop, so a second flush can never be needed — needing two
would mean the queue dropped or deferred work silently.
"""
from typing import override

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode


class _Upper(DerivedNode[str]):
    def __init__(self, node_id, *, src):
        super().__init__(node_id, str, (src,))

    @override
    @staticmethod
    def compute(src: Attempt) -> Attempt:
        val = src.value_or_none()
        if val is None:
            return Attempt.pending()
        return Attempt.succeeded(str(val).upper())


class _Decorated(DerivedNode[str]):
    def __init__(self, node_id, *, src):
        super().__init__(node_id, str, (src,))

    @override
    def compute(self, src: Attempt) -> Attempt:
        val = src.value_or_none()
        if val is None:
            return Attempt.pending()
        return Attempt.succeeded(f"<<{val}>>")


@pytest.mark.asyncio
async def test_single_flush_propagates_two_level_cascade():
    """A push + ONE flush must resolve a grandchild node."""
    loc = UserInputNode("sf_loc", str)
    upper = _Upper("sf_upper", src=loc)
    decorated = _Decorated("sf_deco", src=upper)

    loc.push("abc", "test")
    await flush_processor()

    a = await decorated.attempt()
    assert a.succeeded, f"one flush must drain the whole cascade, got {a.status}: {a.error}"
    ua = await upper.attempt()
    print("DEBUG upper:", ua.status, repr(ua.value_or_none()))
    assert a.value_or_none() == "<<ABC>>"
