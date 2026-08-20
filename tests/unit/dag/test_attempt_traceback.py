"""Auto-capture of the active exception traceback on Attempt.impossible().

Services do `except Exception as e: return Attempt.impossible(...)` — the
user-facing error is the message, but the traceback must survive for
debugging (observability-before-corrective-action). It is captured from
sys.exc_info() at construction time and serialised separately as
error_traceback, never mixed into the frontend-facing error string.
"""
from __future__ import annotations

import asyncio

from dag.attempt import Attempt, Provenance
from dag.node import Node


class TestAutoCapture:
    def test_captures_traceback_inside_except(self):
        try:
            raise ValueError("boom")
        except ValueError:
            a = Attempt.impossible("lookup failed")
        assert a.impossible
        assert a.error == "lookup failed"
        assert "ValueError" in a.traceback
        assert "boom" in a.traceback
        assert "test_attempt_traceback" in a.traceback

    def test_no_traceback_outside_except(self):
        a = Attempt.impossible("plain reason")
        assert a.traceback == ""

    def test_captures_nested_service_frame(self):
        """The traceback must include the service frame that caught the
        exception, so the failing call site is identifiable."""

        def _service():
            try:
                raise TimeoutError("TfL timed out")
            except TimeoutError:
                return Attempt.impossible("could not route transit")

        a = _service()
        assert "TimeoutError" in a.traceback
        assert "_service" in a.traceback
        assert "TfL timed out" in a.traceback

    def test_succeeded_attempt_has_no_traceback(self):
        a = Attempt.succeeded(42)
        assert a.traceback == ""

    def test_impossible_created_inside_except_then_reused(self):
        """Capture happens at construction; the returned Attempt keeps it."""
        try:
            raise RuntimeError("api down")
        except RuntimeError:
            a = Attempt.impossible("api failure")
        # Constructing other attempts later must not change the captured one
        b = Attempt.impossible("unrelated")
        assert "api down" in a.traceback
        assert b.traceback == ""


class _ImpossibleNode(Node[str]):
    """Minimal Node subclass with a pre-set impossible attempt."""

    def __init__(self, node_id: str, attempt: Attempt[str]):
        super().__init__(node_id, str)
        self._test_attempt = attempt

    async def attempt(self):
        return self._test_attempt

    async def build_provenance(self):
        return Provenance(label=self._id)


class TestSerialization:
    def test_error_detail_in_to_json_when_present(self):
        try:
            raise KeyError("missing-field")
        except KeyError:
            a = Attempt.impossible("no data")

        j = asyncio.run(_ImpossibleNode("tb_test", a).to_json())
        assert j["status"] == "impossible"
        # User-facing error is the exception text, not the internal message
        assert "missing-field" in j["error"]
        detail = j["error_detail"]
        assert detail["code"] == "exception"
        assert "KeyError" in detail["traceback"]
        assert "missing-field" in detail["traceback"]

    def test_plain_error_emits_no_data_detail(self):
        a = Attempt.impossible("plain")
        j = asyncio.run(_ImpossibleNode("tb_test2", a).to_json())
        assert j["status"] == "impossible"
        assert j["error"] == "plain"
        assert j["error_detail"]["code"] == "no_data"
        assert j["error_detail"]["traceback"] == ""
        assert j["error_detail"]["exc_type"] == ""

    def test_succeeded_has_no_error_detail_key(self):
        a = Attempt.succeeded("ok")
        j = asyncio.run(_ImpossibleNode("tb_test3", a).to_json())
        assert "error_detail" not in j


class TestStructuredError:
    """The structured error must carry the actual exception object —
    not just a string — so code can inspect status/headers/cause."""

    def test_error_info_holds_exception_object(self):
        try:
            raise ValueError("boom")
        except ValueError as e:
            a = Attempt.impossible("lookup failed")
            captured = e  # noqa: F841 — same object as exc_info[1]
        info = a.error_info
        assert info is not None
        assert isinstance(info.exc, ValueError)
        assert "boom" in str(info.exc)
        assert info.code == "exception"
        assert info.retryable is False

    def test_error_info_holds_http_exception(self):
        from dag.http_error import HttpError

        try:
            raise HttpError(429, headers={"retry-after": "10"})
        except HttpError:
            a = Attempt.impossible("rate limited")
        info = a.error_info
        assert info is not None
        assert isinstance(info.exc, HttpError)
        assert info.exc.status == 429
        assert info.exc.retry_after == 10.0
        assert info.code == "http_error"
        assert info.retryable is True  # 429 → retryable, no string parsing

    def test_plain_message_has_no_data_code(self):
        a = Attempt.impossible("plain")
        info = a.error_info
        assert info is not None
        assert info.code == "no_data"
        assert info.exc is None
        assert info.traceback == ""
        # plain message outside except: no exception, no traceback
        assert a.traceback == ""

    def test_to_dict_is_json_safe(self):
        import json

        try:
            raise ValueError("boom")
        except ValueError:
            a = Attempt.impossible("lookup failed")
        d = a.error_info.to_dict()
        # JSON-serializable (no exception object inside)
        json.dumps(d)
        assert d["exc_type"] == "ValueError"
        assert "ValueError" in d["traceback"]


class TestCausesChain:
    """Parent errors must carry their failed dependencies' structured
    errors, so the chain is traversable without string parsing."""

    def test_impossible_chain_builds_causes(self):
        try:
            raise ValueError("root failure")
        except ValueError:
            leaf = Attempt.impossible("leaf failed")

        from dag.attempt import AttemptError

        parent_msg = "parent: dep failed (leaf failed)"
        parent = Attempt.impossible(
            parent_msg,
            error_info=AttemptError(
                code="dep_failed",
                message=parent_msg,
                source="parent",
                causes=(leaf.error_info,),
            ),
        )
        info = parent.error_info
        assert info.code == "dep_failed"
        assert len(info.causes) == 1
        cause = info.causes[0]
        assert isinstance(cause.exc, ValueError)
        assert cause.message == "leaf failed"
        # The chain is structural: parent.causes[0].exc, no string search
        assert "root failure" in str(cause.exc)

    def test_node_impossible_propagates_causes(self):
        from dag.node import Node

        class _Leaf(Node[str]):
            def __init__(self, attempt):
                super().__init__("leaf", str)
                self._a = attempt

            async def attempt(self):
                return self._a

            async def build_provenance(self):
                from dag.attempt import Provenance

                return Provenance(label="leaf")

            def compute(self):
                raise AssertionError("unused")

        try:
            raise TimeoutError("tfl down")
        except TimeoutError:
            leaf_attempt = Attempt.impossible("transit failed")

        leaf = _Leaf(leaf_attempt)
        result = leaf._impossible({"transit": leaf_attempt})
        info = result.error_info
        assert info is not None
        assert info.code == "dep_failed"
        assert len(info.causes) == 1
        assert isinstance(info.causes[0].exc, TimeoutError)
        assert info.causes[0].retryable is True


class TestUserFacingMessages:
    """The UI must see friendly leaf messages, never node ids or
    'dep failed' framework markers. The internal chain stays in
    error_detail for debugging."""

    def test_leaf_service_message_is_user_facing(self):
        # Service writes a friendly message (no exception captured)
        a = Attempt.impossible("Works estimate required for: Ashby")
        assert a.error_info.user_message == ""
        assert a.error_info.display_message == "Works estimate required for: Ashby"

    def test_exception_text_is_user_facing_when_captured(self):
        try:
            raise ValueError("no matching certificate for this address")
        except ValueError:
            a = Attempt.impossible("lookup failed")
        assert a.error_info.display_message == "no matching certificate for this address"

    def test_dep_chain_error_is_friendly_leaf(self):
        from dag.attempt import AttemptError

        leaf = AttemptError(code="no_data", message="Works estimate required for: Ashby")
        mid = AttemptError(
            code="dep_failed",
            message="mid_node: dep failed (Works estimate required for: Ashby)",
            causes=(leaf,),
        )
        root = AttemptError(
            code="dep_failed",
            message="root_node: dep failed (mid_node: dep failed (Works estimate required for: Ashby))",
            user_message=mid.display_message,
            causes=(mid,),
        )
        # Friendly message survives the chain
        assert root.display_message == "Works estimate required for: Ashby"
        # Internal chain is separate and debuggable
        assert "dep failed" in root.message
        assert "root_node" in root.message

    def test_to_json_error_is_friendly_through_chain(self):
        """A 3-level node chain must surface the leaf message as error,
        with node ids only in error_detail."""
        from dag.attempt import AttemptError

        leaf_attempt = Attempt.impossible("Works estimate required for: Ashby")
        mid_info = AttemptError(
            code="dep_failed",
            message="89306649/mortgage_required: dep failed (Works estimate required for: Ashby)",
            user_message=leaf_attempt.error_info.display_message,
            causes=(leaf_attempt.error_info,),
        )
        root_info = AttemptError(
            code="dep_failed",
            message="89306649/total_monthly_cost: dep failed (89306649/mortgage_required: dep failed (...))",
            user_message=mid_info.display_message,
            causes=(mid_info,),
        )
        j = root_info.to_dict()
        assert j["user_message"] == "Works estimate required for: Ashby"
        assert "dep failed" in j["message"]
        assert "89306649" in j["message"]
        assert "89306649" not in j["user_message"]
        assert "dep failed" not in j["user_message"]

    def test_http_error_friendly_user_message_preferred_over_raw_str(self):
        """HttpError str() may embed the raw response body; the explicit
        friendly user_message must win as the UI text (walkthrough run 3 —
        a raw 'HTTP 404: {$type: ...}' blob was rendered to the user)."""
        from dag.attempt import AttemptError
        from dag.http_error import HttpError

        raw_body = "{'$type': 'Tfl.Api.Presentation.Entities.ApiError, Tfl.Api.Presentation.Entities', 'httpStatusCode': 404}"  # noqa: E501
        exc = HttpError(
            404,
            message=raw_body,
            body=raw_body,
            user_message="TfL couldn't find a route for this journey",
        )
        info = AttemptError.from_exception(f"node: {exc}", exc, source="node")
        assert info.display_message == "TfL couldn't find a route for this journey"
        assert raw_body not in info.display_message
        # The internal message keeps the raw reason for logs.
        assert raw_body in info.message

    def test_http_error_without_user_message_falls_back_to_str(self):
        from dag.attempt import AttemptError
        from dag.http_error import HttpError

        exc = HttpError(429, "rate limited")
        info = AttemptError.from_exception(f"node: {exc}", exc, source="node")
        assert info.display_message == "HTTP 429: rate limited"

    def test_http_error_user_message_survives_persistence_round_trip(self):
        from dag.attempt import AttemptError
        from dag.http_error import HttpError

        exc = HttpError(
            409,
            message="{'message': 'route planner unavailable'}",
            body="{'message': 'route planner unavailable'}",
            user_message="TfL's route planner is unavailable right now",
        )
        info = AttemptError.from_exception(f"node: {exc}", exc, source="node")
        restored = AttemptError.from_dict(info.to_dict())
        assert restored.display_message == "TfL's route planner is unavailable right now"
        assert "route planner unavailable" in restored.message
