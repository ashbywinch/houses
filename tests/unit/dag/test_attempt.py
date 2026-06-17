from __future__ import annotations

from dag.attempt import Attempt, Provenance


class TestAttempt:
    def test_succeeded(self):
        a = Attempt.succeeded(42, Provenance("test"))
        assert a.is_succeeded is True
        assert a.value_or_none() == 42
        assert a._error == ""

    def test_impossible(self):
        a = Attempt.impossible("something went wrong")
        assert a.is_succeeded is False
        assert a.value_or_none() is None
        assert a._error == "something went wrong"

    def test_provenance_on_succeeded(self):
        prov = Provenance("Rightmove")
        a = Attempt.succeeded("10 High St", prov)
        assert a.provenance is prov
        assert a.provenance.label == "Rightmove"

    def test_provenance_on_impossible(self):
        a = Attempt.impossible("failed")
        assert a.provenance.label == ""

    def test_source_attempts(self):
        inner = Attempt.succeeded(42, Provenance("inner"))
        outer_prov = Provenance("outer", source_attempts={"dep": inner})
        outer = Attempt.succeeded(99, outer_prov)
        assert outer.provenance.source_attempts["dep"] is inner

    def test_string_value(self):
        a = Attempt.succeeded("hello", Provenance("test"))
        assert a.value_or_none() == "hello"

    def test_float_value(self):
        a = Attempt.succeeded(3.14, Provenance("test"))
        assert a.value_or_none() == 3.14

    def test_none_value_on_impossible(self):
        a = Attempt.impossible("error")
        assert a.value_or_none() is None

    def test_deep_provenance_chain(self):
        leaf = Attempt.succeeded(51.5, Provenance("Rightmove map"))
        geo = Attempt.succeeded(
            leaf.value_or_none(),
            Provenance("Geocoded", source_attempts={"rightmove_location": leaf}),
        )
        best = Attempt.succeeded(
            geo.value_or_none(),
            Provenance("User location", source_attempts={"geocode_location": geo}),
        )
        assert best.provenance.source_attempts["geocode_location"].provenance.label == "Geocoded"

    def test_equality_ignores_provenance(self):
        a1 = Attempt.succeeded("hello", Provenance("src1"))
        a2 = Attempt.succeeded("hello", Provenance("src2"))
        assert a1.value_or_none() == a2.value_or_none()


class TestProvenance:
    def test_default_label(self):
        p = Provenance()
        assert p.label == ""

    def test_label_only(self):
        p = Provenance("TfL API")
        assert p.label == "TfL API"

    def test_source_attempts_dict(self):
        p = Provenance("test", source_attempts={"dep_a": Attempt.impossible("fail")})
        assert "dep_a" in p.source_attempts
