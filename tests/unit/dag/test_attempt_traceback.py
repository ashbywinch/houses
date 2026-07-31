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
    def test_error_traceback_in_to_json_when_present(self):
        try:
            raise KeyError("missing-field")
        except KeyError:
            a = Attempt.impossible("no data")

        j = asyncio.run(_ImpossibleNode("tb_test", a).to_json())
        assert j["status"] == "impossible"
        assert j["error"] == "no data"
        assert "KeyError" in j["error_traceback"]
        # error_traceback must be separate from the frontend-facing error
        assert "missing-field" not in j["error"]
        assert "missing-field" in j["error_traceback"]

    def test_no_error_traceback_key_when_absent(self):
        a = Attempt.impossible("plain")
        j = asyncio.run(_ImpossibleNode("tb_test2", a).to_json())
        assert j["status"] == "impossible"
        assert j["error"] == "plain"
        assert "error_traceback" not in j

    def test_succeeded_has_no_traceback_key(self):
        a = Attempt.succeeded("ok")
        j = asyncio.run(_ImpossibleNode("tb_test3", a).to_json())
        assert "error_traceback" not in j
