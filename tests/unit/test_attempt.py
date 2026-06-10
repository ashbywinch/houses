"""Tests for Attempt[T] — constructors, predicates, extraction, transform, match."""

import pytest

from houses.attempt import Attempt


class TestConstructors:
    def test_succeeded_has_value_and_source(self):
        attempt = Attempt.succeeded(42, "test-source")
        assert attempt.is_succeeded
        assert not attempt.is_pending
        assert not attempt.is_impossible
        assert attempt.get() == 42
        assert attempt.value_or(0) == 42
        assert attempt.value_or_none() == 42

    def test_pending_has_no_value(self):
        attempt = Attempt.pending()
        assert not attempt.is_succeeded
        assert attempt.is_pending
        assert not attempt.is_impossible
        with pytest.raises(ValueError, match="PENDING"):
            attempt.get()
        assert attempt.value_or(0) == 0
        assert attempt.value_or_none() is None

    def test_impossible_has_reason(self):
        exc = ValueError("something broke")
        attempt = Attempt.impossible("test-source", "could not connect", exc)
        assert not attempt.is_succeeded
        assert not attempt.is_pending
        assert attempt.is_impossible
        with pytest.raises(ValueError, match="IMPOSSIBLE"):
            attempt.get()
        assert attempt.value_or(0) == 0
        assert attempt.value_or_none() is None

    def test_impossible_without_exception(self):
        attempt = Attempt.impossible("test-source", "not found")
        assert attempt.is_impossible
        assert attempt.value_or_none() is None

    def test_succeeded_with_string_value(self):
        attempt = Attempt.succeeded("hello", "str-source")
        assert attempt.get() == "hello"

    def test_succeeded_with_none_value(self):
        attempt = Attempt.succeeded(None, "none-source")
        assert attempt.is_succeeded
        assert attempt.get() is None


class TestMap:
    def test_map_transforms_succeeded(self):
        attempt = Attempt.succeeded(5, "src")
        mapped = attempt.map(lambda x: x * 2)
        assert mapped.is_succeeded
        assert mapped.get() == 10

    def test_map_preserves_source(self):
        attempt = Attempt.succeeded(5, "my-source")
        mapped = attempt.map(lambda x: x * 2)
        assert (
            mapped.match(
                succeeded=lambda v, src: src,
                pending=lambda: "",
                impossible=lambda *_: "",
            )
            == "my-source"
        )

    def test_map_passes_through_pending(self):
        attempt: Attempt[int] = Attempt.pending()
        mapped = attempt.map(lambda x: x * 2)
        assert mapped.is_pending

    def test_map_passes_through_impossible(self):
        attempt: Attempt[int] = Attempt.impossible("src", "fail")
        mapped = attempt.map(lambda x: x * 2)
        assert mapped.is_impossible

    def test_map_changes_type(self):
        attempt = Attempt.succeeded(5, "src")
        mapped = attempt.map(lambda x: str(x))
        assert (
            mapped.match(
                succeeded=lambda v, src: v,
                pending=lambda: "",
                impossible=lambda *_: "",
            )
            == "5"
        )


class TestBind:
    def test_bind_chains_succeeded(self):
        attempt = Attempt.succeeded(5, "src")
        bound = attempt.bind(lambda x: Attempt.succeeded(x * 2, "doubler"))
        assert bound.is_succeeded
        assert bound.get() == 10

    def test_bind_passes_through_pending(self):
        attempt: Attempt[int] = Attempt.pending()
        bound = attempt.bind(lambda x: Attempt.succeeded(x * 2, "doubler"))
        assert bound.is_pending

    def test_bind_passes_through_impossible(self):
        attempt: Attempt[int] = Attempt.impossible("src", "fail")
        bound = attempt.bind(lambda x: Attempt.succeeded(x * 2, "doubler"))
        assert bound.is_impossible

    def test_bind_can_return_impossible(self):
        attempt = Attempt.succeeded(5, "src")
        bound = attempt.bind(lambda x: Attempt.impossible("src", "invalid value"))
        assert bound.is_impossible


class TestMatch:
    def test_match_succeeded(self):
        attempt = Attempt.succeeded(42, "src")
        result = attempt.match(
            succeeded=lambda v, src: f"ok:{v}",
            pending=lambda: "pending",
            impossible=lambda *_: "fail",
        )
        assert result == "ok:42"

    def test_match_pending(self):
        attempt: Attempt[int] = Attempt.pending()
        result = attempt.match(
            succeeded=lambda v, src: "ok",
            pending=lambda: "pending",
            impossible=lambda *_: "fail",
        )
        assert result == "pending"

    def test_match_impossible(self):
        attempt: Attempt[int] = Attempt.impossible("src", "timeout")
        result = attempt.match(
            succeeded=lambda v, src: "ok",
            pending=lambda: "pending",
            impossible=lambda src, reason, exc: f"fail:{reason}",
        )
        assert result == "fail:timeout"

    def test_match_impossible_includes_exception(self):
        exc = RuntimeError("boom")
        attempt = Attempt.impossible("src", "crash", exc)
        result = attempt.match(
            succeeded=lambda v, src: None,
            pending=lambda: None,
            impossible=lambda src, reason, e: e,
        )
        assert result is exc


class TestValueOr:
    def test_value_or_returns_value_when_succeeded(self):
        attempt = Attempt.succeeded(42, "src")
        assert attempt.value_or(-1) == 42

    def test_value_or_returns_default_when_pending(self):
        attempt: Attempt[int] = Attempt.pending()
        assert attempt.value_or(-1) == -1

    def test_value_or_returns_default_when_impossible(self):
        attempt: Attempt[int] = Attempt.impossible("src", "fail")
        assert attempt.value_or(-1) == -1

    def test_value_or_with_string_default(self):
        attempt: Attempt[str] = Attempt.pending()
        assert attempt.value_or("fallback") == "fallback"


class TestValueOrNone:
    def test_value_or_none_returns_value_when_succeeded(self):
        attempt = Attempt.succeeded(99, "src")
        assert attempt.value_or_none() == 99

    def test_value_or_none_returns_none_when_pending(self):
        attempt: Attempt[int] = Attempt.pending()
        assert attempt.value_or_none() is None

    def test_value_or_none_returns_none_when_impossible(self):
        attempt: Attempt[int] = Attempt.impossible("src", "fail")
        assert attempt.value_or_none() is None


class TestEquality:
    def test_succeeded_instances_are_equal(self):
        a1 = Attempt.succeeded(1, "src")
        a2 = Attempt.succeeded(1, "src")
        assert a1 == a2

    def test_pending_instances_are_equal(self):
        assert Attempt.pending() == Attempt.pending()

    def test_succeeded_and_pending_are_not_equal(self):
        assert Attempt.succeeded(1, "src") != Attempt.pending()
