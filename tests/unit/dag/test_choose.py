"""Tests for Choose expression — selection with provenance transparency."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from money import Money

from dag.attempt import Attempt
from dag.expression import Choose, Ref


@dataclass
class FakeNode:
    _id: str = "test"
    display_name: str = ""
    _attempt: Attempt | None = None

    def latest_attempt(self) -> Attempt | None:
        return self._attempt


def _ref(value, label="opt"):
    att = Attempt.succeeded(value) if not isinstance(value, Attempt) else value
    return Ref(FakeNode(_id=label, display_name=label, _attempt=att))


class TestChoose:
    def test_selects_winner_from_two(self):
        """Choose returns the value selected by the selector function."""
        a = _ref(Money("100", "GBP"), "cheap")
        b = _ref(Money("200", "GBP"), "expensive")

        expr = Choose(
            alternatives={"cheap": a, "expensive": b},
            selector=lambda results: "cheap",
        )
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("100", "GBP")

    def test_returns_selected_value(self):
        """The selected alternative's value is returned."""
        a = _ref(Decimal("10"), "a")
        b = _ref(Decimal("20"), "b")

        expr = Choose(
            alternatives={"a": a, "b": b},
            selector=lambda results: "b" if results["b"].value_or_none() > results["a"].value_or_none() else "a",
        )
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Decimal("20")

    def test_impossible_when_no_winner(self):
        """If selector returns None, Choose returns impossible."""
        a = _ref(Decimal("10"), "a")

        expr = Choose(
            alternatives={"a": a},
            selector=lambda results: None,
        )
        result = expr.evaluate()
        assert result.impossible

    def test_propagates_alternative_failure(self):
        """If a chosen alternative failed, the failure propagates."""
        a = _ref(Attempt.impossible("broken"), "a")
        b = _ref(Decimal("20"), "b")

        expr = Choose(
            alternatives={"a": a, "b": b},
            selector=lambda results: "a",
        )
        result = expr.evaluate()
        assert result.impossible

    def test_propagates_alternative_failure_even_when_not_selected(self):
        """If an unchosen alternative failed, Choose still works (just notes it)."""
        a = _ref(Decimal("10"), "a")
        b = _ref(Attempt.impossible("broken"), "b")

        expr = Choose(
            alternatives={"a": a, "b": b},
            selector=lambda results: "a",
        )
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Decimal("10")

    def test_formula_shows_all_alternatives(self):
        """Each alternative appears in formula lines with ✓/✗."""
        a = _ref(Money("200", "GBP"), "transit")
        b = _ref(Money("100", "GBP"), "drive")

        expr = Choose(
            alternatives={"transit": a, "drive": b},
            selector=lambda results: (
                "drive" if results["drive"].value_or_none() < results["transit"].value_or_none() else "transit"
            ),
        )
        expr.evaluate()  # compute so to_formula_lines has data
        lines = expr.to_formula_lines()
        assert len(lines) == 2
        # All alternatives should appear
        labels = " ".join(_l.label for _l in lines)
        assert "transit" in labels
        assert "drive" in labels

    def test_selector_receives_attempts_not_raw_values(self):
        """Selector receives Attempt objects so it can check .succeeded etc."""
        a = _ref(Decimal("10"), "a")
        b = _ref(Attempt.impossible("fail"), "b")

        def selector(results):
            assert hasattr(results["a"], "succeeded")
            assert hasattr(results["b"], "impossible")
            return "a"

        expr = Choose(alternatives={"a": a, "b": b}, selector=selector)
        result = expr.evaluate()
        assert result.succeeded

    def test_multiple_alternatives(self):
        """Works with three or more alternatives."""
        a = _ref(Decimal("10"), "walk")
        b = _ref(Decimal("30"), "transit")
        c = _ref(Decimal("60"), "drive")

        expr = Choose(
            alternatives={"walk": a, "transit": b, "drive": c},
            selector=lambda results: min(results, key=lambda k: results[k].value_or_none()),
        )
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Decimal("10")  # walk is cheapest

    def test_exposes_last_results(self):
        """After evaluate(), .last_results exposes all scores for provenance."""
        a = _ref(Decimal("100"), "a")
        b = _ref(Decimal("200"), "b")

        expr = Choose(
            alternatives={"a": a, "b": b},
            selector=lambda results: "a",
        )
        expr.evaluate()
        assert expr.last_results is not None
        assert "a" in expr.last_results
        assert "b" in expr.last_results
        assert expr.last_results["a"].succeeded
        assert expr.last_results["a"].value_or_none() == Decimal("100")
