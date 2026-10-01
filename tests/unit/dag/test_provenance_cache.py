"""Read-cache contract: the serve path must not re-read the DB per call.

Once a node's provenance is persisted, every serve-path read must come
from the in-memory cache — a property detail serializes dozens of nodes,
so a DB fetch per node per request is exactly the per-page seconds the
serve contract ("no re-read, no recompute") forbids.
"""

from __future__ import annotations

from typing import Any, override

import pytest

import dag.derived_node as dn
from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode

CACHE_BOUND = 16
CACHE_OVERFILL = 21


class _Single(DerivedNode[int]):
    @staticmethod
    @override
    def compute(value: Attempt[int]):
        return value


@pytest.fixture(autouse=True)
def _clear_cache():
    # The cache is process-global and bounded; tests must not observe
    # each other's entries.
    dn._PROVENANCE_CACHE.clear()
    yield
    dn._PROVENANCE_CACHE.clear()


@pytest.mark.asyncio
async def test_serve_reads_are_db_free_after_persist():
    a = UserInputNode("svcache_a", int)
    s = _Single("svcache_s", int, (a,), dep_names=("value",))
    a.push(1, "test")
    await flush_processor()  # persist populates the cache

    calls = 0
    real = dn.latest_node_result

    def _spy(node_id: str) -> dict[str, Any] | None:
        nonlocal calls
        calls += 1
        return real(node_id)

    dn.latest_node_result = _spy  # type: ignore[assignment]  # the assertion is that serve never calls it
    try:
        prov = await s.build_provenance()  # serve path, dep_attempts=None
        assert prov.sources["svcache_a"].value is not None
        assert calls == 0, "serve read after persist must not re-read the DB"
        await s.build_provenance()
        assert calls == 0, "repeat serve reads must stay cache-served"
    finally:
        dn.latest_node_result = real


@pytest.mark.asyncio
async def test_persist_refreshes_the_cached_tree():
    """A re-persist immediately serves the NEW tree, never the old one."""
    a = UserInputNode("svcache2_a", int)
    s = _Single("svcache2_s", int, (a,), dep_names=("value",))
    a.push(1, "test")
    await flush_processor()

    prov_before = await s.build_provenance()

    a.push(2, "test")
    await flush_processor()  # re-persists with value 2

    prov_after = await s.build_provenance()
    assert prov_after.to_dict() != prov_before.to_dict(), "refresh must replace the cached tree"
    assert prov_after.sources["svcache2_a"].value == 2


@pytest.mark.asyncio
async def test_cache_is_bounded():
    a = UserInputNode("svcache3_a", int)
    _Single("svcache3_s", int, (a,), dep_names=("value",))
    a.push(1, "test")
    await flush_processor()

    saved = dn._PROVENANCE_CACHE_MAX
    # the bound is a runtime knob; shrinking it makes eviction observable in a few rows
    dn._PROVENANCE_CACHE_MAX = CACHE_BOUND  # type: ignore[assignment]
    try:
        for i in range(CACHE_OVERFILL):
            a2 = UserInputNode(f"svcache_b{i}_a", int)
            o = _Single(f"svcache_b{i}", int, (a2,), dep_names=("value",))
            a2.push(1, "test")
            await flush_processor()
            await o.build_provenance()
    finally:
        dn._PROVENANCE_CACHE_MAX = saved
    assert len(dn._PROVENANCE_CACHE) <= CACHE_BOUND, "the cache must never grow past its bound"
