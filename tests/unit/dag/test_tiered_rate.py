"""Tests for TieredRate expression and Node operator integration."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from money import Money

from dag.attempt import Attempt
from houses.nodes.expressions import TieredRate


@dataclass
class FakeNode:
    _id: str = "test"
    display_name: str = ""
    _attempt: Attempt | None = None

    def latest_attempt(self) -> Attempt | None:
        return self._attempt

    def __add__(self, other):  # pragma: no cover — tested via operator tests
        from dag.node import _node_add
        return _node_add(self, other)


class TestTieredRate:
    def test_stamp_duty_at_200k(self):
        """£200,000 — below threshold, £0 stamp duty."""
        price = FakeNode(_id="price", display_name="price",
                         _attempt=Attempt.succeeded(Money("200000", "GBP")))
        expr = TieredRate(price, tiers=[
            (0, 250000, 0),
            (250000, 925000, Decimal("0.05")),
            (925000, 1500000, Decimal("0.10")),
            (1500000, None, Decimal("0.12")),
        ])
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("0", "GBP")

    def test_stamp_duty_at_500k(self):
        """£500,000 — £250k at 0% + £250k at 5% = £12,500."""
        price = FakeNode(_id="price", display_name="price",
                         _attempt=Attempt.succeeded(Money("500000", "GBP")))
        expr = TieredRate(price, tiers=[
            (0, 250000, 0),
            (250000, 925000, Decimal("0.05")),
            (925000, 1500000, Decimal("0.10")),
            (1500000, None, Decimal("0.12")),
        ])
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("12500", "GBP")

    def test_stamp_duty_at_800k(self):
        """£800,000 — £250k at 0% + £550k at 5% = £27,500."""
        price = FakeNode(_id="price", display_name="price",
                         _attempt=Attempt.succeeded(Money("800000", "GBP")))
        expr = TieredRate(price, tiers=[
            (0, 250000, 0),
            (250000, 925000, Decimal("0.05")),
            (925000, 1500000, Decimal("0.10")),
            (1500000, None, Decimal("0.12")),
        ])
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("27500", "GBP")

    def test_stamp_duty_at_1m(self):
        """£1,000,000 — £250k at 0% + £675k at 5% + £75k at 10% = £41,250."""
        price = FakeNode(_id="price", display_name="price",
                         _attempt=Attempt.succeeded(Money("1000000", "GBP")))
        expr = TieredRate(price, tiers=[
            (0, 250000, 0),
            (250000, 925000, Decimal("0.05")),
            (925000, 1500000, Decimal("0.10")),
            (1500000, None, Decimal("0.12")),
        ])
        result = expr.evaluate()
        assert result.succeeded
        # 0 + 33750 + (1000000-925000)*0.10 = 33750 + 7500 = 41250
        assert result.value == Money("41250", "GBP")

    def test_stamp_duty_at_2m(self):
        """£2,000,000 — hits all 4 tiers: 0 + 33750 + 57500 + 60000 = 151,250."""
        price = FakeNode(_id="price", display_name="price",
                         _attempt=Attempt.succeeded(Money("2000000", "GBP")))
        expr = TieredRate(price, tiers=[
            (0, 250000, 0),
            (250000, 925000, Decimal("0.05")),
            (925000, 1500000, Decimal("0.10")),
            (1500000, None, Decimal("0.12")),
        ])
        result = expr.evaluate()
        assert result.succeeded
        assert result.value == Money("151250", "GBP")

    def test_propagates_node_failure(self):
        """If the price node is impossible, the expression fails."""
        price = FakeNode(_id="price", display_name="price",
                         _attempt=Attempt.impossible("no price"))
        expr = TieredRate(price, tiers=[(0, None, 0)])
        result = expr.evaluate()
        assert result.impossible

    def test_formula_lines_for_800k(self):
        """Formula should show the tiers and highlight the active one."""
        price = FakeNode(_id="price", display_name="price",
                         _attempt=Attempt.succeeded(Money("800000", "GBP")))
        expr = TieredRate(price, tiers=[
            (0, 250000, 0),
            (250000, 925000, Decimal("0.05")),
            (925000, 1500000, Decimal("0.10")),
            (1500000, None, Decimal("0.12")),
        ])
        lines = expr.to_formula_lines()
        assert len(lines) >= 2  # price + at least one tier
        assert any("800,000" in _l.value for _l in lines)
        assert any("250,000" in _l.label for _l in lines)
