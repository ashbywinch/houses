"""Tests for the Expression system — Ref now takes Node objects, not strings."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from money import Money

from dag.attempt import Attempt
from dag.expression import Conditional, Literal, Ref
from houses.nodes.expressions import PMT, StampDutyFn

# ── Test helper: a minimal node-like object ──


@dataclass
class FakeNode:
    """Quacks like a DAG Node for testing expressions."""
    _id: str = "test_node"
    display_name: str = ""
    _attempt: Attempt | None = None
    _source_url: str = ""

    def latest_attempt(self) -> Attempt | None:
        return self._attempt

    def to_json(self):
        return {}

    def to_json_value(self):
        return {}


def _ref(value, label="price"):
    """Build a Ref to a FakeNode with the given attempt value."""
    att = Attempt.succeeded(value) if not isinstance(value, Attempt) else value
    return Ref(FakeNode(_id=label, display_name=label, _attempt=att))


# ── Tests ──


class TestRef:
    def test_resolves_succeeded_attempt(self):
        node = FakeNode(_id="price", display_name="price",
                        _attempt=Attempt.succeeded(Money("500000", "GBP")))
        expr = Ref(node)
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("500000", "GBP")

    def test_missing_attempt_returns_impossible(self):
        node = FakeNode(_id="price", display_name="price", _attempt=None)
        expr = Ref(node)
        result = expr.evaluate()
        assert result.impossible
        assert "not yet computed" in result.error

    def test_impossible_dep_propagates(self):
        node = FakeNode(_id="price", display_name="price",
                        _attempt=Attempt.impossible("not found"))
        expr = Ref(node)
        result = expr.evaluate()
        assert result.impossible
        assert "not found" in result.error

    def test_to_formula_lines(self):
        node = FakeNode(_id="price", display_name="price",
                        _attempt=Attempt.succeeded(Money("500000", "GBP")))
        expr = Ref(node)
        lines = expr.to_formula_lines()
        assert len(lines) == 1
        assert lines[0].label == "price"
        assert "500,000" in lines[0].value


class TestLiteral:
    def test_returns_value(self):
        expr = Literal(Money("100", "GBP"))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("100", "GBP")

    def test_formula_line(self):
        expr = Literal(Money("100", "GBP"))
        lines = expr.to_formula_lines()
        assert len(lines) == 1
        assert "100" in lines[0].value


class TestAdd:
    def test_adds_two_values(self):
        expr = _ref(Decimal("100")) + _ref(Decimal("50"))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Decimal("150")

    def test_adds_three_values(self):
        expr = _ref(Decimal("10")) + _ref(Decimal("20")) + _ref(Decimal("30"))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Decimal("60")

    def test_adds_money_values(self):
        expr = _ref(Money("100", "GBP")) + _ref(Money("50", "GBP"))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("150", "GBP")

    def test_failure_propagates(self):
        expr = _ref(Decimal("10")) + _ref(Attempt.impossible("failed"))
        result = expr.evaluate()
        assert result.impossible
        assert "failed" in result.error

    def test_all_terms_in_formula_lines(self):
        expr = _ref(Decimal("10")) + _ref(Decimal("20")) + _ref(Decimal("30"))
        lines = expr.to_formula_lines()
        assert len(lines) == 3

    def test_result_in_formula(self):
        expr = _ref(Decimal("10")) + _ref(Decimal("20"))
        formula = expr.to_formula()
        assert formula is not None
        assert "30" in formula.result
        assert len(formula.lines) == 2


class TestSub:
    def test_subtracts(self):
        expr = _ref(Decimal("100")) - _ref(Decimal("30"))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Decimal("70")

    def test_money_subtract(self):
        expr = _ref(Money("500", "GBP")) - _ref(Money("100", "GBP"))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("400", "GBP")

    def test_formula_lines(self):
        expr = _ref(Decimal("100")) - _ref(Decimal("30"))
        lines = expr.to_formula_lines()
        assert len(lines) == 2


class TestNegate:
    def test_negates_value(self):
        expr = -_ref(Decimal("50"))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Decimal("-50")

    def test_negates_money(self):
        expr = -_ref(Money("100", "GBP"))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("-100", "GBP")

    def test_failure_propagates(self):
        expr = -_ref(Attempt.impossible("fail"))
        result = expr.evaluate()
        assert result.impossible


class TestMul:
    def test_multiplies(self):
        expr = _ref(Decimal("100")) * _ref(Decimal("0.01"))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Decimal("1")

    def test_money_times_scalar(self):
        expr = _ref(Money("800000", "GBP")) * _ref(Decimal("0.01"))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("8000.00", "GBP")


class TestDiv:
    def test_divides(self):
        expr = _ref(Money("6000", "GBP")) / Literal(12)
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("500", "GBP")

    def test_formula_lines(self):
        expr = _ref(Money("6000", "GBP")) / Literal(12)
        lines = expr.to_formula_lines()
        assert len(lines) >= 1


class TestPMT:
    def test_calculates_monthly_payment(self):
        """£415,000 at 4.95% over 27 years → ~£2,305/mo"""
        expr = PMT(
            principal=_ref(Money("415000", "GBP")),
            annual_rate=_ref(Decimal("0.0495")),
            term_years=_ref(27),
        )
        result = expr.evaluate()
        assert result.succeeded
        val = result.value
        assert val is not None and 2000 < float(val.amount) < 2500

    def test_formula_shows_steps(self):
        expr = PMT(
            principal=_ref(Money("415000", "GBP")),
            annual_rate=_ref(Decimal("0.0495")),
            term_years=_ref(27),
        )
        formula = expr.to_formula()
        assert formula is not None
        assert len(formula.lines) >= 3
        assert any("price" in _l.label.lower() for _l in formula.lines)
        assert "415,000" in formula.lines[0].value

    def test_failure_propagates(self):
        expr = PMT(
            principal=_ref(Money("415000", "GBP")),
            annual_rate=_ref(Attempt.impossible("rate missing")),
            term_years=_ref(27),
        )
        result = expr.evaluate()
        assert result.impossible


class TestConditional:
    def test_condition_true_uses_if_branch(self):
        flag = True
        expr = Conditional(
            predicate=lambda: flag,
            if_true=_ref(Decimal("10")),
            if_false=_ref(Decimal("20")),
        )
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Decimal("10")

    def test_condition_false_uses_else_branch(self):
        flag = False
        expr = Conditional(
            predicate=lambda: flag,
            if_true=_ref(Decimal("10")),
            if_false=_ref(Decimal("20")),
        )
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Decimal("20")

    def test_unused_branch_failure_does_not_propagate(self):
        expr = Conditional(
            predicate=lambda: True,
            if_true=_ref(Decimal("10")),
            if_false=_ref(Attempt.impossible("broken")),
        )
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Decimal("10")


class TestStampDutyFn:
    def test_calculates_stamp_duty(self):
        """£800,000 property → £27,500 stamp duty (5% on £550k after £250k threshold)"""
        from money import Money
        expr = StampDutyFn(_ref(Money("800000", "GBP")))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("27500", "GBP")

    def test_failure_propagates(self):
        expr = StampDutyFn(_ref(Attempt.impossible("no price")))
        result = expr.evaluate()
        assert result.impossible

    def test_to_formula_lines(self):
        price_node = FakeNode(_id="price", display_name="price",
                              _attempt=Attempt.succeeded(Money("800000", "GBP")))
        expr = StampDutyFn(Ref(price_node))
        lines = expr.to_formula_lines()
        assert len(lines) == 1
        assert "price" in lines[0].label


class TestExpressionIntegration:
    def test_complex_expression_chain_with_operators(self):
        """Simulate mortgage_required = price + stamp_duty + works - equity"""
        eight = _ref(Money("800000", "GBP"))
        twenty = _ref(Money("27500", "GBP"))
        fifty = _ref(Money("50000", "GBP"))
        four = _ref(Money("477000", "GBP"))
        expr = eight + twenty + fifty - four
        result = expr.evaluate()
        assert result.succeeded
        val = result.value
        assert val is not None and float(val.amount) == 400500

    def test_partial_failure_in_chain(self):
        """If works is impossible, the whole expression fails but formula still shows all terms."""
        eight = _ref(Money("800000", "GBP"))
        twenty = _ref(Money("27500", "GBP"))
        imp = _ref(Attempt.impossible("estimate required"))
        four = _ref(Money("477000", "GBP"))
        expr = eight + twenty + imp - four
        result = expr.evaluate()
        assert result.impossible

        # Formula should still show all terms
        formula = expr.to_formula()
        assert formula is not None
        # The available terms appear in formula lines
        assert any("price" in _l.label for _l in formula.lines)
