"""Tests for the Measurement value wrapper (value + uncertainty).

Part A — uncertainty ("≈") as a first-class DAG citizen. The wrapper
must combine values AND errors through the same operators, so nodes
built with plain arithmetic are correct by construction.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from money import Money

from dag.attempt import Attempt, project_value
from dag.expression import Choose, Literal, Ref
from dag.measurement import Measurement
from dag.scheduler import AsyncQueueScheduler, reset_scheduler, set_scheduler

# ── Test helper: a minimal node-like object (mirrors test_expression.py) ──


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


def _ref(value, label="n"):
    """Build a Ref to a FakeNode with the given attempt value."""
    att = Attempt.succeeded(value) if not isinstance(value, Attempt) else value
    return Ref(FakeNode(_id=label, display_name=label, _attempt=att))


# ── Arithmetic propagation ─────────────────────────────────────────


class TestArithmetic:
    def test_add_combines_values_and_errors_in_quadrature(self):
        # (100 ± 1) + (200 ± 2) = 300 ± sqrt(1² + 2²) = 300 ± √5
        m = Measurement(100, 1.0) + Measurement(200, 2.0)
        assert m.value == 300
        assert m.stddev == pytest.approx(5**0.5)

    def test_subtract_combines_errors_in_quadrature(self):
        # (200 ± 2) − (100 ± 1) = 100 ± √5
        m = Measurement(200, 2.0) - Measurement(100, 1.0)
        assert m.value == 100
        assert m.stddev == pytest.approx(5**0.5)

    def test_multiply_by_exact_scalar_scales_error(self):
        # (100 ± 1) × 2 = 200 ± 2
        m = Measurement(100, 1.0) * Measurement(2, 0.0)
        assert m.value == 200
        assert m.stddev == pytest.approx(2.0)

    def test_multiply_two_uncertain_values(self):
        # (100 ± 1) × (200 ± 2): relative errors 1% and 1% → combined
        # relative error ≈ sqrt(0.01² + 0.01²) ≈ 1.414% → 20000 * 0.01414
        m = Measurement(100, 1.0) * Measurement(200, 2.0)
        assert m.value == 20000
        assert m.stddev == pytest.approx(20000 * (0.01**2 + 0.01**2) ** 0.5, rel=1e-3)

    def test_divide_by_exact_scalar(self):
        # (100 ± 1) ÷ 4 = 25 ± 0.25
        m = Measurement(100, 1.0) / Measurement(4, 0.0)
        assert m.value == 25
        assert m.stddev == pytest.approx(0.25)

    def test_negate_keeps_error(self):
        m = -Measurement(100, 1.0)
        assert m.value == -100
        assert m.stddev == pytest.approx(1.0)

    def test_plain_number_operands_wrap_as_exact(self):
        # Measurement + plain number / number * Measurement — the DAG
        # mixes literals (e.g. "× 52 weeks") with measured values.
        assert (Measurement(100, 1.0) + 5).value == 105
        assert (Measurement(100, 1.0) + 5).stddev == pytest.approx(1.0)
        assert (2 * Measurement(100, 1.0)).value == 200
        assert (2 * Measurement(100, 1.0)).stddev == pytest.approx(2.0)
        assert (Measurement(100, 1.0) / 4).stddev == pytest.approx(0.25)


class TestExactness:
    def test_exact_has_zero_uncertainty(self):
        m = Measurement(42)
        assert m.stddev == 0.0

    def test_mixing_exact_with_approximate_keeps_approximate_error(self):
        # Exact + approximate must not widen or narrow the spread.
        m = Measurement(5, 0.0) + Measurement(5, 1.0)
        assert m.value == 10
        assert m.stddev == pytest.approx(1.0)


# ── Serialization / provenance ────────────────────────────────────


class TestSerialization:
    def test_to_provenance_value_round_trips_json_safe(self):
        m = Measurement(Money("1200", "GBP"), 50.0)
        pv = project_value(m)
        assert pv == {"value": str(Money("1200", "GBP")), "uncertainty": 50.0}

    def test_provenance_value_is_json_serializable(self):
        import json

        m = Measurement(Money("1200", "GBP"), 50.0)
        assert json.dumps(project_value(m))  # must not raise


# ── Flows through expressions (Ref / Add / Choose) ────────────────


class TestExpressionFlow:
    def test_ref_passes_measured_attempt_through(self):
        m = Measurement(Money("1200", "GBP"), 50.0)
        result = _ref(m).evaluate()
        assert result.succeeded
        assert result.value == m

    def test_add_expression_propagates_measurement(self):
        expr = _ref(Measurement(Money("1200", "GBP"), 50.0)) + _ref(Measurement(Money("300", "GBP"), 10.0))
        result = expr.evaluate()
        assert result.succeeded
        assert result.value is not None
        assert result.value.value == Money("1500", "GBP")
        assert result.value.stddev == pytest.approx((50.0**2 + 10.0**2) ** 0.5)

    def test_choose_returns_winner_measurement_unchanged(self):
        # Precision-aware guarantee: the chosen branch's measurement
        # travels; the loser's uncertainty never enters (A2 seed).
        loser = Measurement(Money("100", "GBP"), 50.0)
        winner = Measurement(Money("200", "GBP"), 5.0)
        expr = Choose(
            alternatives={"a": _ref(loser), "b": _ref(winner)},
            selector=lambda results: (
                "b" if results["b"].value.value.amount >= results["a"].value.value.amount else "a"
            ),
        )
        result = expr.evaluate()
        assert result.succeeded
        assert result.value is not None
        assert result.value == winner
        assert result.value.stddev == pytest.approx(5.0)

    def test_literal_formats_measurement_in_formula_lines(self):
        lines = Literal(Measurement(Money("1200", "GBP"), 50.0)).to_formula_lines()
        assert "1,200" in lines[0].value
        assert "50" in lines[0].value


# ── Selection nodes: precision-aware guarantee (A2) ─────────────
# Choose / IfThenElseNode return the chosen branch's measurement
# unchanged — the loser's uncertainty never enters.


@pytest.fixture(autouse=True)
def _isolated_scheduler():
    set_scheduler(AsyncQueueScheduler(respect_time=False))
    yield
    reset_scheduler()


class TestSelectionNodes:
    @pytest.mark.asyncio
    async def test_if_then_else_true_passes_winner_measurement_unchanged(self):
        from dag.if_then_else import IfThenElseNode
        from dag.scheduler import flush_processor
        from dag.user_input_node import UserInputNode

        cond = UserInputNode[bool]("cond_a2", bool)
        then_src = UserInputNode[Measurement[Money]]("then_a2", Measurement[Money])
        else_src = UserInputNode[Measurement[Money]]("else_a2", Measurement[Money])

        node = IfThenElseNode(
            "ite_a2",
            Measurement[Money],
            condition_sources=(cond,),
            condition_fn=lambda a: a.value_or(False),
            then_branch=then_src,
            else_branch=else_src,
        )

        cond.push(True, "user")
        then_src.push(Measurement(Money("200", "GBP"), 5.0), "user")
        else_src.push(Measurement(Money("100", "GBP"), 500.0), "user")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        assert val == Measurement(Money("200", "GBP"), 5.0)
        assert val.stddev == pytest.approx(5.0)

    @pytest.mark.asyncio
    async def test_if_then_else_false_passes_else_measurement_unchanged(self):
        from dag.if_then_else import IfThenElseNode
        from dag.scheduler import flush_processor
        from dag.user_input_node import UserInputNode

        cond = UserInputNode[bool]("cond_a2b", bool)
        then_src = UserInputNode[Measurement[Money]]("then_a2b", Measurement[Money])
        else_src = UserInputNode[Measurement[Money]]("else_a2b", Measurement[Money])

        node = IfThenElseNode(
            "ite_a2b",
            Measurement[Money],
            condition_sources=(cond,),
            condition_fn=lambda a: a.value_or(False),
            then_branch=then_src,
            else_branch=else_src,
        )

        cond.push(False, "user")
        then_src.push(Measurement(Money("200", "GBP"), 500.0), "user")
        else_src.push(Measurement(Money("100", "GBP"), 5.0), "user")

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        assert val == Measurement(Money("100", "GBP"), 5.0)
        assert val.stddev == pytest.approx(5.0)

    def test_choose_with_impossible_loser_returns_winner_measurement(self):
        winner = Measurement(Money("200", "GBP"), 5.0)
        expr = Choose(
            alternatives={
                "good": _ref(winner),
                "bad": _ref(Attempt.impossible("no route found")),
            },
            selector=lambda results: "good" if results["good"].succeeded else "bad",
        )
        result = expr.evaluate()
        assert result.succeeded
        assert result.value is not None
        assert result.value == winner
        assert result.value.stddev == pytest.approx(5.0)
