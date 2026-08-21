from __future__ import annotations

from typing import override

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode
from dag.persistence import latest_node_result
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode


class TestDerivedNode:
    @pytest.mark.asyncio
    async def test_recomputes_on_dep_change(self):
        src = UserInputNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        src.push(2, "test")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 4

        src.push(3, "test")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 6

    @pytest.mark.asyncio
    async def test_initial_attempt_runs_compute(self):
        src = UserInputNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        a = await node.attempt()
        assert a.succeeded is False

        src.push(5, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.value_or_none() == 10

    @pytest.mark.asyncio
    async def test_caches_result_until_dep_changes(self):
        src = UserInputNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        src.push(10, "test")
        await flush_processor()
        first = await node.attempt()
        assert first.value_or_none() == 20

        second = await node.attempt()
        assert second.value_or_none() == 20
        assert node.compute_count == 1

    @pytest.mark.asyncio
    async def test_multiple_deps(self):
        a = UserInputNode[int]("a", int)
        b = UserInputNode[int]("b", int)
        node = _SumNode("sum", deps=(a, b))

        a.push(3, "t")
        b.push(4, "t")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 7

        a.push(10, "t")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 14

    @pytest.mark.asyncio
    async def test_changed_signal_fires_on_recompute(self):
        src = UserInputNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        received = []
        node.changed.connect(lambda: received.append("changed"))

        src.push(2, "test")
        await flush_processor()
        assert received == ["changed"]

        src.push(3, "test")
        await flush_processor()
        assert received == ["changed", "changed"]

    @pytest.mark.asyncio
    async def test_impossible_when_dep_fails(self):
        """When a dep returns Attempt.impossible, the derived node should also be impossible."""

        # A node that returns impossible
        class _FailingNode(DerivedNode[int]):
            def __init__(self):
                super().__init__("fail_src", int, ())
                self._attempt = Attempt.impossible("always fails")

            @override
            def compute(self):
                return self._attempt

        src = _FailingNode()
        await flush_processor()

        doubler = _DoubleNode("double_fail_test", deps=(src,))
        await flush_processor()
        a = await doubler.attempt()
        assert a.impossible is True

    @pytest.mark.asyncio
    async def test_impossible_dep_crash_preserves_provenance(self):
        """When compute() crashes from using None from an impossible dep,
        the framework catches it and preserves provenance in the error record."""

        class _FailingNode(DerivedNode[int]):
            def __init__(self):
                super().__init__("fail_crash_src", int, ())
                self._attempt = Attempt.impossible("always fails")

            @override
            def compute(self):
                return self._attempt

        class _CrashNode(DerivedNode[int]):
            def __init__(self, node_id: str, deps):
                super().__init__(node_id, int, deps)

            @override
            def compute(self, *args):
                return Attempt.succeeded((args[0].value_or_none() or 0) + 1)

            @override
            async def build_provenance(self):
                return Provenance(label="crash_test")

        src = _FailingNode()
        await flush_processor()

        node = _CrashNode("crash_test", deps=(src,))
        await flush_processor()

        a = await node.attempt()
        assert a.impossible is True
        assert "dep failed" in a.error

        stored = latest_node_result("crash_test")
        assert stored is not None
        prov = stored.get("provenance", {})
        assert prov.get("label") == "crash_test"

    @pytest.mark.asyncio
    async def test_to_json(self):
        src = UserInputNode[int]("src", int)
        node = _DoubleNode("double", deps=(src,))

        src.push(4, "test")
        await flush_processor()
        j = await node.to_json()
        assert j["value"] == 8

    @pytest.mark.asyncio
    async def test_persists_after_compute(self):
        src = UserInputNode[int]("src_persist", int)
        node = _DoubleNode("double_persist", deps=(src,))

        src.push(7, "test")
        await flush_processor()
        await node.attempt()

        loaded = latest_node_result("double_persist")
        assert loaded is not None
        assert loaded["status"] == "succeeded"
        assert loaded["value"] == 14

    @pytest.mark.asyncio
    async def test_loads_from_db_on_init(self):
        src = UserInputNode[int]("src_reload", int)
        node1 = _DoubleNode("double_reload", deps=(src,))
        src.push(9, "test")
        await flush_processor()
        await node1.attempt()

        src2 = UserInputNode[int]("src_reload", int)
        node2 = _DoubleNode("double_reload", deps=(src2,))
        a = await node2.attempt()
        assert a.value_or_none() == 18

    @pytest.mark.asyncio
    async def test_staleness_timestamp_dep_push(self):
        src = UserInputNode[int]("src_stale", int)
        node = _DoubleNode("double_stale", deps=(src,))

        src.push(5, "test")
        await flush_processor()
        await node.attempt()
        assert node.compute_count == 1

        src.push(10, "test")
        await flush_processor()
        await node.attempt()
        assert node.compute_count == 2

    @pytest.mark.asyncio
    async def test_async_compute(self):
        src = UserInputNode[int]("src_async", int)
        node = _AsyncDoubleNode("double_async", deps=(src,))

        src.push(3, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.value_or_none() == 6

    @pytest.mark.asyncio
    async def test_dep_timestamps_not_returned_by_latest(self):
        src = UserInputNode[int]("src_dep_ts", int)
        node = _DoubleNode("double_dep_ts", deps=(src,))

        src.push(42, "test")
        await flush_processor()
        await node.attempt()

        loaded = latest_node_result("double_dep_ts")
        assert loaded is not None
        assert loaded["status"] == "succeeded"
        assert loaded["value"] == 84

    @pytest.mark.asyncio
    async def test_http_error_raised_in_compute_surfaces_friendly_message(self):
        """A compute that raises HttpError (the TfL path) must persist a
        friendly user_message — the raw body stays in the internal
        message/logs only (walkthrough run 3)."""
        src = UserInputNode[str]("src_http_err", str)
        node = _RaisingHttpNode("http_err_node", deps=(src,))
        src.push("x", "test")
        await flush_processor()

        a = await node.attempt()
        assert a.impossible
        assert a.error_info is not None
        assert a.error_info.display_message == "TfL couldn't find a route for this journey"
        assert "$type" in a.error_info.message  # internal message keeps the raw reason

        j = await node.to_json()
        assert j["error"] == "TfL couldn't find a route for this journey"
        assert "$type" not in j["error"]
        assert j["error_detail"]["user_message"] == "TfL couldn't find a route for this journey"

        loaded = latest_node_result("http_err_node")
        assert loaded is not None
        assert loaded["error"] == "TfL couldn't find a route for this journey"
        assert "$type" not in loaded["error"]


class _DoubleNode(DerivedNode[int]):
    def __init__(self, node_id: str, deps):
        super().__init__(node_id, int, deps)
        self.compute_count = 0

    @override
    def compute(self, *dep_attempts) -> Attempt[int]:
        self.compute_count += 1
        val = dep_attempts[0]
        v = val.value_or_none()
        if val.succeeded and v is not None:
            return Attempt.succeeded(v * 2)
        return Attempt.impossible("dep failed")


class _RaisingHttpNode(DerivedNode[str]):
    """Mimics a service call that raises HttpError (e.g. TfL 404)."""

    def __init__(self, node_id: str, deps):
        super().__init__(node_id, str, deps)

    @override
    async def compute(self, *dep_attempts) -> Attempt[str]:
        from dag.http_error import HttpError

        raw = "{'$type': 'Tfl.Api.Presentation.Entities.ApiError', 'httpStatusCode': 404}"
        raise HttpError(
            404,
            message=raw,
            body=raw,
            user_message="TfL couldn't find a route for this journey",
        )


class _SumNode(DerivedNode[int]):
    def __init__(self, node_id: str, deps):
        super().__init__(node_id, int, deps)
        self.compute_count = 0

    @override
    def compute(self, *dep_attempts) -> Attempt[int]:
        self.compute_count += 1
        vals = [a.value_or_none() for a in dep_attempts]
        if all(a.succeeded for a in dep_attempts):
            return Attempt.succeeded(sum(v for v in vals if v is not None))
        return Attempt.impossible("one or more deps failed")


class _AsyncDoubleNode(DerivedNode[int]):
    def __init__(self, node_id: str, deps):
        super().__init__(node_id, int, deps)

    @override
    async def compute(self, *dep_attempts) -> Attempt[int]:
        val = dep_attempts[0]
        v = val.value_or_none()
        if val.succeeded and v is not None:
            return Attempt.succeeded(v * 2)
        return Attempt.impossible("dep failed")


class TestCommuteDetailsRoundTrip:
    """Commute details persisted under `details` (the selector's frontend
    rename, and legacy model rows) must survive reload — never silently
    dropped."""

    @pytest.mark.asyncio
    async def test_details_key_survives_reload(self):
        from pydantic import TypeAdapter

        from dag.persistence import save_node_result
        from houses.commute import CostGroup, JourneyLeg, LegMode
        from houses.model.domain import Commute, Person, PlaceOfInterest

        commute = Commute(
            person=Person(name="", has_car=False),
            label="Aldgate",
            destination=PlaceOfInterest(label="Aldgate", address="EC3A"),
            duration=Quantity(156, "minute"),  # type: ignore[arg-type]
            daily_cost=Money("100", "GBP"),
            mode="transit",
            _details=(
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(42, "minute")),),  # type: ignore[arg-type]
                    operator="TfL",
                    cost=Money("100", "GBP"),
                ),
            ),
        )
        value_dict = TypeAdapter(Commute).dump_python(commute, mode="json")
        # The selector's to_json renames _details → details; that is what
        # gets persisted. Simulate exactly that shape.
        value_dict["details"] = value_dict.pop("_details")
        save_node_result("rt_legacy_details", {"status": "succeeded", "value": value_dict})

        from houses.nodes.commute import CommuteSelectorNode

        node = CommuteSelectorNode(
            "rt_legacy_details",
            origin=UserInputNode("rt_origin", object),
            poi=UserInputNode("rt_poi", object),
            transit_result=UserInputNode("rt_transit", object),
            is_child=False,
            max_walk_node=UserInputNode("rt_mw", int),
        )
        a = node.latest_attempt()
        assert a.succeeded, f"reload must succeed, got: {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        assert len(val._details) == 1, f"commute details must survive reload, got: {val._details!r}"
        assert val._details[0].legs[0].mode == LegMode.TRAIN


class TestNamedDepsDispatch:
    """Nodes with ``dep_names`` get their dep attempts bound by NAME, so a
    ``_get_active_deps`` that drops a MIDDLE dep cannot shift every later
    argument into the wrong parameter (the historical group-node bug)."""

    class _NamedNode(DerivedNode[int]):
        def __init__(self, node_id, a, b, c):
            super().__init__(node_id, int, (a, b, c), dep_names=("a", "b", "c"))
            self.received: tuple | None = None

        @override
        def _get_active_deps(self):
            # Drop the MIDDLE dep — positionally this would bind b's
            # attempt to the `c` parameter.
            return (self._deps[0], self._deps[2])

        @override
        def compute(self, a=None, b=None, c=None):
            self.received = (a, b, c)
            av = a.value_or_none() if a is not None else 0
            cv = c.value_or_none() if c is not None else 0
            return Attempt.succeeded(av + cv)

    @pytest.mark.asyncio
    async def test_middle_dep_drop_binds_by_name_not_position(self):
        a = UserInputNode[int]("nda", int)
        b = UserInputNode[int]("ndb", int)
        c = UserInputNode[int]("ndc", int)
        node = self._NamedNode("named_mid", a, b, c)

        a.push(1, "t")
        c.push(2, "t")
        await flush_processor()

        assert node.received is not None
        assert node.received[1] is None, (
            f"the dropped middle dep must stay None — the c attempt must not land in b's slot: {node.received}"
        )
        assert node.received[2] is not None
        assert (await node.attempt()).value_or_none() == 3


class TestComputeArityGuard:
    """A node whose positional deps outnumber its compute parameters must
    fail LOUDLY (a ValueError naming the node), never silently misbind."""

    class _TooManyDepsNode(DerivedNode[int]):
        def __init__(self, node_id, deps):
            super().__init__(node_id, int, deps)

        @override
        def compute(self, a, b):
            return Attempt.succeeded(0)

    @pytest.mark.asyncio
    async def test_positional_arity_mismatch_fails_with_named_error(self):
        a = UserInputNode[int]("ga", int)
        b = UserInputNode[int]("gb", int)
        c = UserInputNode[int]("gc", int)
        node = self._TooManyDepsNode("arity_bad", (a, b, c))

        a.push(1, "t")
        b.push(2, "t")
        c.push(3, "t")
        await flush_processor()
        attempt = await node.attempt()
        assert attempt.impossible, (
            f"a positional arity drift must fail loudly, not silently misbind — got: {attempt.status}: {attempt.error}"
        )
        assert "arity_bad" in (attempt.error or "")
        assert "drifted" in (attempt.error or "")


class TestCodeVersionStaleness:
    """A persisted result computed by DIFFERENT code must recompute even
    when every dep timestamp is fresh — the gap that let the stale
    'takes 9 to 11 arguments' errors sit on live properties."""

    @pytest.mark.asyncio
    async def test_refresh_recomputes_when_persisted_code_version_differs(self):
        src = UserInputNode[int]("cv_src", int)
        node = _DoubleNode("cv_double", deps=(src,))

        src.push(2, "test")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 4
        assert node.compute_count == 1

        # Simulate a code change: the persisted row carries a version the
        # current compute no longer matches. Dep timestamps are unchanged.
        node._persisted_code_version = "old-code-hash"
        await node.refresh()
        assert node.compute_count == 2, "a code-version mismatch must recompute despite fresh dep timestamps"

    @pytest.mark.asyncio
    async def test_legacy_row_without_code_version_is_code_stale(self):
        src = UserInputNode[int]("cv_src2", int)
        node = _DoubleNode("cv_double2", deps=(src,))

        src.push(3, "test")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == 6

        # Pre-migration rows have no code version at all — "" means
        # "unknown code", which must recompute once.
        node._persisted_code_version = ""
        assert node.code_is_stale() is True
