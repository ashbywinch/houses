"""Tests for Attempt, Provenance, and related types."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dag.attempt import Attempt, Formula, FormulaLine, Provenance, SourceType


class TestSourceType:
    def test_str_values(self):
        assert SourceType.API.value == "api"
        assert SourceType.CALC.value == "calc"
        assert SourceType.USER.value == "user"
        assert SourceType.CONFIG.value == "config"
        assert SourceType.GEOCODE.value == "geocode"
        assert SourceType.DB.value == "db"


class TestFormulaLine:
    def test_basic(self):
        fl = FormulaLine(label="Price", value="250000")
        assert fl.label == "Price"
        assert fl.value == "250000"


class TestFormula:
    def test_lines_and_result(self):
        f = Formula(
            lines=[FormulaLine(label="x", value="1"), FormulaLine(label="y", value="2")],
            result="3",
        )
        assert len(f.lines) == 2
        assert f.result == "3"


class TestProvenanceToDict:
    def test_includes_source_type(self):
        prov = Provenance(label="test", source_type=SourceType.API)
        d = prov.to_dict()
        assert d["sourceType"] == "api"

    def test_omits_source_type_when_none(self):
        prov = Provenance(label="test")
        d = prov.to_dict()
        assert "sourceType" not in d

    def test_includes_freshness(self):
        dt = datetime(2024, 1, 15, 14, 23, tzinfo=UTC)
        prov = Provenance(label="test", freshness=dt)
        d = prov.to_dict()
        assert d["freshness"] == "2024-01-15T14:23:00+00:00"

    def test_omits_freshness_when_none(self):
        prov = Provenance(label="test")
        d = prov.to_dict()
        assert "freshness" not in d

    def test_includes_formula(self):
        prov = Provenance(
            label="test",
            formula=Formula(lines=[FormulaLine(label="x", value="1")], result="2"),
        )
        d = prov.to_dict()
        assert d["formula"] == {"lines": [{"label": "x", "value": "1"}], "result": "2"}

    def test_omits_formula_when_none(self):
        prov = Provenance(label="test")
        d = prov.to_dict()
        assert "formula" not in d

    def test_includes_url(self):
        prov = Provenance(label="test", url="https://example.com")
        d = prov.to_dict()
        assert d["url"] == "https://example.com"

    def test_omits_url_when_empty(self):
        prov = Provenance(label="test")
        d = prov.to_dict()
        assert "url" not in d

    def test_value_serialized(self):
        prov = Provenance(label="test", value=42)
        d = prov.to_dict()
        assert d["value"] == 42

    def test_sources_nested(self):
        child = Provenance(label="child")
        parent = Provenance(label="parent", sources={"a": child})
        d = parent.to_dict()
        assert d["sources"]["a"]["label"] == "child"

    def test_non_json_value_stringified(self):
        prov = Provenance(label="test", value=object())
        d = prov.to_dict()
        assert isinstance(d["value"], str)


class TestAttemptCreatedAt:
    def test_created_at_is_datetime(self):
        a = Attempt.succeeded(42)
        assert isinstance(a.created_at, datetime)

    def test_created_at_defaults_to_now(self):
        before = datetime.now(UTC)
        a = Attempt.succeeded(42)
        after = datetime.now(UTC)
        assert before <= a.created_at <= after

    def test_created_at_can_be_overridden_for_testing(self):
        fixed = datetime(2024, 1, 15, 14, 23, tzinfo=UTC)
        saved = Attempt._now
        try:
            Attempt._now = lambda: fixed
            a = Attempt.succeeded(42)
            assert a.created_at == fixed
        finally:
            Attempt._now = saved

    def test_created_at_set_on_pending(self):
        a = Attempt.pending()
        assert isinstance(a.created_at, datetime)

    def test_created_at_set_on_impossible(self):
        a = Attempt.impossible("oops")
        assert isinstance(a.created_at, datetime)

    def test_created_at_immutable_via_slots(self):
        a = Attempt.succeeded(42)
        with pytest.raises(AttributeError):
            a.created_at = datetime.now(UTC)


class TestFormulaJsonShape:
    """Verify the JSON shape matches the frontend Provenance type."""

    def test_formula_via_to_dict(self):
        prov = Provenance(
            label="Monthly payment",
            formula=Formula(
                lines=[
                    FormulaLine(label="Price", value="250000"),
                    FormulaLine(label="Interest rate", value="4.5%"),
                ],
                result="1267.89",
            ),
        )
        d = prov.to_dict()
        assert "formula" in d
        assert d["formula"]["lines"] == [
            {"label": "Price", "value": "250000"},
            {"label": "Interest rate", "value": "4.5%"},
        ]
        assert d["formula"]["result"] == "1267.89"
