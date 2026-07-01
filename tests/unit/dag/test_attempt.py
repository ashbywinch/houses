from __future__ import annotations

from dag.attempt import Attempt


class TestAttempt:
    def test_succeeded(self):
        a = Attempt.succeeded(42)
        assert a.succeeded is True
        assert a.value_or_none() == 42
        assert a.error == ""

    def test_impossible(self):
        a = Attempt.impossible("something went wrong")
        assert a.succeeded is False
        assert a.pending is False
        assert a.impossible is True
        assert a.value_or_none() is None
        assert a.error == "something went wrong"

    def test_pending(self):
        a = Attempt.pending()
        assert a.succeeded is False
        assert a.pending is True
        assert a.impossible is False
        assert a.value_or_none() is None

    def test_string_value(self):
        a = Attempt.succeeded("hello")
        assert a.value_or_none() == "hello"

    def test_float_value(self):
        a = Attempt.succeeded(3.14)
        assert a.value_or_none() == 3.14

    def test_none_value_on_impossible(self):
        a = Attempt.impossible("error")
        assert a.value_or_none() is None

    def test_status_string_succeeded(self):
        a = Attempt.succeeded(42)
        assert a.status == "succeeded"

    def test_status_string_pending(self):
        a = Attempt.pending()
        assert a.status == "pending"

    def test_status_string_impossible(self):
        a = Attempt.impossible("fail")
        assert a.status == "impossible"

    def test_value_or_returns_default_on_failure(self):
        a = Attempt.impossible("fail")
        assert a.value_or(99) == 99

    def test_value_or_returns_value_on_success(self):
        a = Attempt.succeeded(42)
        assert a.value_or(99) == 42

    def test_get_raises_on_impossible(self):
        import pytest
        a = Attempt.impossible("fail")
        with pytest.raises(ValueError):
            a.get()

    def test_get_returns_value_on_success(self):
        a = Attempt.succeeded(42)
        assert a.get() == 42

    def test_map_transforms_succeeded(self):
        a = Attempt.succeeded(42)
        b = a.map(lambda x: x * 2)
        assert b.value_or_none() == 84

    def test_map_passes_through_impossible(self):
        a = Attempt.impossible("fail")
        b = a.map(lambda x: x * 2)
        assert b.impossible

    def test_bind_chains_succeeded(self):
        a = Attempt.succeeded(42)
        b = a.bind(lambda x: Attempt.succeeded(x * 2))
        assert b.value_or_none() == 84

    def test_bind_passes_through_impossible(self):
        a = Attempt.impossible("fail")
        b = a.bind(lambda x: Attempt.succeeded(x * 2))
        assert b.impossible

    def test_match_succeeded(self):
        a = Attempt.succeeded(42)
        result = a.match(
            on_succeeded=lambda v: f"value={v}",
            on_pending=lambda: "pending",
            on_impossible=lambda e: f"error={e}",
        )
        assert result == "value=42"

    def test_match_pending(self):
        a = Attempt.pending()
        result = a.match(
            on_succeeded=lambda v: f"value={v}",
            on_pending=lambda: "pending",
            on_impossible=lambda e: f"error={e}",
        )
        assert result == "pending"

    def test_match_impossible(self):
        a = Attempt.impossible("oops")
        result = a.match(
            on_succeeded=lambda v: f"value={v}",
            on_pending=lambda: "pending",
            on_impossible=lambda e: f"error={e}",
        )
        assert result == "error=oops"


class TestProvenance:
    def test_default_label(self):
        from dag.attempt import Provenance
        p = Provenance()
        assert p.label == ""

    def test_label_only(self):
        from dag.attempt import Provenance
        p = Provenance("TfL API")
        assert p.label == "TfL API"

    def test_from_label(self):
        from dag.attempt import Provenance
        p = Provenance.from_label("Rightmove")
        assert p.label == "Rightmove"

    def test_composite(self):
        from dag.attempt import Provenance
        inner = Provenance("inner")
        outer = Provenance.composite("outer", {"dep": inner})
        assert outer.label == "outer"
        assert outer.sources["dep"].label == "inner"

    def test_to_dict_flat(self):
        from dag.attempt import Provenance
        p = Provenance("test")
        d = p.to_dict()
        assert d == {"label": "test"}

    def test_to_dict_with_description(self):
        from dag.attempt import Provenance
        p = Provenance("test", description="hello")
        d = p.to_dict()
        assert d == {"label": "test", "description": "hello"}

    def test_to_dict_with_sources(self):
        from dag.attempt import Provenance
        inner = Provenance("inner")
        outer = Provenance.composite("outer", {"dep": inner})
        d = outer.to_dict()
        assert d == {"label": "outer", "sources": {"dep": {"label": "inner"}}}
