"""Serve contract: build_provenance() with no bound attempts returns the
frozen row verbatim — no dep-row walk, no live re-read."""

from __future__ import annotations

from typing import override

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode


class _Single(DerivedNode[int]):
    @staticmethod
    @override
    def compute(value: Attempt[int]):
        return value


@pytest.mark.asyncio
async def test_serve_returns_frozen_row_without_dep_walk():
    a = UserInputNode("sv2_a", int)
    s = _Single("sv2_s", int, (a,), dep_names=("value",))
    a.push(1, "test")
    await flush_processor()

    async def _boom(self, *args, **kwargs):
        raise AssertionError("serve must not walk dep rows")

    a.build_provenance = _boom  # type: ignore[method-assign]
    try:
        prov = await s.build_provenance()
    finally:
        del a.build_provenance
    assert prov.sources["sv2_a"].value is not None, "the frozen row carries the dep subtree"


@pytest.mark.asyncio
async def test_serve_returns_exact_persisted_tree():
    """Serve output equals the row's stored provenance field, byte for byte."""
    from dag.persistence import latest_node_result

    a = UserInputNode("sv3_a", int)
    s = _Single("sv3_s", int, (a,), dep_names=("value",))
    a.push(1, "test")
    await flush_processor()

    prov = await s.build_provenance()
    stored = (latest_node_result("sv3_s") or {}).get("provenance", {})
    assert prov.to_dict() == stored, "serve must return the frozen row, not a rebuild"
