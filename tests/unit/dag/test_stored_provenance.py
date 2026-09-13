"""Failing first: provenance renders stored inputs, never live re-reads."""

from __future__ import annotations

from typing import override

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
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


def test_provenance_shows_calculating_inputs_not_live():
    a = UserInputNode("sp_a", int)
    b = UserInputNode("sp_b", int)
    a.push(2, "test")
    b.push(3, "test")
    s = _Sum("sp_sum", a=a, b=b)
    flush_all()
    assert s.latest_attempt().value_or_none() == 5
    # Capture the calculating inputs BEFORE moving on: the point is the
    # provenance row persists them, not that a later value freezes.
    prov = __import__("asyncio").get_event_loop().run_until_complete(s.build_provenance())
    by_id = prov.sources
    assert by_id["sp_a"].value == 2, f"provenance must show the calculating input, got {by_id['sp_a'].value}"
    assert by_id["sp_b"].value == 3
    # And after the dep moves on AND the node refreshes, the NEW row's
    # provenance shows the NEW inputs (each row is self-consistent).
    a.push(20, "test")
    flush_all()
    assert s.latest_attempt().value_or_none() == 23
    prov2 = __import__("asyncio").get_event_loop().run_until_complete(s.build_provenance())
    assert prov2.sources["sp_a"].value == 20, prov2.sources["sp_a"].value


def test_provenance_missing_input_degrades_without_reread():
    a = UserInputNode("sp2_a", int)
    b = UserInputNode("sp2_b", int)
    a.push(2, "test")
    s = _Sum("sp2_sum", a=a, b=b)
    flush_all()
    prov = __import__("asyncio").get_event_loop().run_until_complete(s.build_provenance())
    assert prov.sources["sp2_b"].status == "pending", prov.sources["sp2_b"]


def test_provenance_never_rereads_live_deps():
    """The point of stored inputs: build_provenance must not call
    dep.build_provenance (a live re-read) when a stored input exists.
    A dep whose live provenance would differ must still render stored."""
    a = UserInputNode("sp3_a", int)
    b = UserInputNode("sp3_b", int)
    a.push(2, "test")
    b.push(3, "test")
    s = _Sum("sp3_sum", a=a, b=b)
    flush_all()
    calls: list = []
    orig_a = a.build_provenance

    async def spy():
        calls.append("a")
        return await orig_a()

    a.build_provenance = spy  # type: ignore[method-assign]
    try:
        prov = __import__("asyncio").get_event_loop().run_until_complete(s.build_provenance())
    finally:
        a.build_provenance = orig_a
    assert calls == [], "build_provenance re-read a live dep instead of its stored input"
    assert prov.sources["sp3_a"].value == 2


def test_serve_renders_stored_inputs_not_live_deps():
    """Serve renders the stored calculating inputs, never live dep
    values. Deps move on without this node refreshing; serve still
    shows the frozen inputs."""
    import asyncio
    import json
    import zlib

    a = UserInputNode("spv_a", int)
    b = UserInputNode("spv_b", int)
    a.push(2, "test")
    b.push(3, "test")
    s = _Sum("spv_sum", a=a, b=b)
    flush_all()
    assert s.latest_attempt().value_or_none() == 5
    a.push(20, "test")
    b.push(30, "test")
    from dag import persistence as persistence

    rows = (
        persistence._get_db()
        .execute("SELECT result_json FROM node_results WHERE node_id=? ORDER BY created_at", ("spv_sum",))
        .fetchall()
    )
    assert len(rows) == 1, "premise: s never re-persisted"
    stored_prov = json.loads(zlib.decompress(rows[0][0]).decode())["provenance"]
    served = asyncio.get_event_loop().run_until_complete(s.build_provenance()).to_dict()
    assert served == stored_prov
    assert served["sources"]["spv_a"]["value"] == 2
    assert served["sources"]["spv_b"]["value"] == 3
