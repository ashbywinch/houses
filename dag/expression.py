"""Declarative expression trees for DAG node calculations.

Each Expression knows how to:
1. Evaluate itself (return Attempt[T]) by calling ``latest_attempt()`` on its node refs
2. Produce formula lines (label + value pairs for provenance)
3. Describe itself in plain English

Expressions compose naturally: ``Ref(price_node) + Ref(tax_node)`` creates an ``Add`` tree
the base class can walk for both evaluation and provenance generation.

Every sub-expression that references a dep holds the actual ``Node`` object,
not a string name. No values dict, no zipping, no indirection.
"""

from __future__ import annotations

import traceback
from abc import ABC, abstractmethod
from copy import deepcopy
from decimal import Decimal
from typing import Any, Generic, TypeVar

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine

T = TypeVar("T")


class Expression(ABC, Generic[T]):
    """A declarative expression that can be evaluated AND walked for provenance."""

    description: str = ""
    """Plain-English explanation of what this expression does."""

    @abstractmethod
    def evaluate(self) -> Attempt[T]:
        """Evaluate this expression.

        Walks the expression tree, calling ``latest_attempt()`` on any
        referenced dependency nodes. Returns an ``Attempt``.
        """
        ...

    def _format_value(self, v: Any) -> str:
        """Format a Python value for formula display."""
        if isinstance(v, Money):
            return f"£{v.amount:,.2f}"
        if isinstance(v, Decimal):
            if v < Decimal("0.01"):
                return f"{v:.2%}"
            return f"£{v:,.2f}" if v > Decimal("100") else f"{v:,.2f}"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            return f"{v:,.2f}"
        return str(v)

    def to_formula_lines(self) -> list[FormulaLine]:
        """Produce formula lines showing how this expression was evaluated.

        Called AFTER the node's ``compute()`` has run, so ``latest_attempt()``
        on referenced nodes returns the evaluated values. Override in
        subclasses that represent calculation steps.
        """
        return []

    def to_formula(self) -> Formula | None:
        """Produce a Formula (lines + result) from evaluated values.

        Returns None if this expression has no meaningful formula display.
        """
        result = self.evaluate()
        lines = self.to_formula_lines()
        if not lines:
            return None
        return Formula(
            lines=lines,
            result=self._format_value(result.value) if result.succeeded else "incomplete",
        )

    def to_description(self) -> str:
        """Plain-English explanation of what this expression does."""
        return self.description

    # ── Operator overloading ─────────────────────────────
    # These let you write Ref(price_node) + Ref(tax_node) instead
    # of Add(Ref(price_node), Ref(tax_node)).

    def __add__(self, other: Expression) -> Add:
        return Add(self, other)

    def __radd__(self, other: Expression) -> Add:
        return Add(other, self)

    def __sub__(self, other: Expression) -> Sub:
        return Sub(self, other)

    def __rsub__(self, other: Expression) -> Sub:
        return Sub(other, self)

    def __neg__(self) -> Negate:
        return Negate(self)

    def __mul__(self, other: Expression) -> Mul:
        return Mul(self, other)

    def __rmul__(self, other: Expression) -> Mul:
        return Mul(other, self)

    def __truediv__(self, other: Expression) -> Div:
        return Div(self, other)

    def __rtruediv__(self, other: Expression) -> Div:
        return Div(other, self)


# ── Leaf Expressions ──


class Literal(Expression[T]):
    """A constant value. No dependencies."""

    def __init__(self, value: T, description: str = ""):
        self._value = value
        self.description = description

    def evaluate(self) -> Attempt[T]:
        return Attempt.succeeded(deepcopy(self._value))

    def to_formula_lines(self) -> list[FormulaLine]:
        return [FormulaLine(label="Value", value=self._format_value(self._value))]


class Ref(Expression[T]):
    """Reference a dependency Node. Calls ``latest_attempt()`` directly."""

    def __init__(self, node, description: str = ""):
        from dag.node import Node as _Node

        self.node: _Node = node
        self.description = description

    @property
    def _label(self) -> str:
        return getattr(self.node, "display_name", self.node._id)

    def evaluate(self) -> Attempt[T]:
        try:
            attempt = self.node.latest_attempt()
        except RuntimeError:
            return Attempt.impossible(f"Node {self.node._id} does not support sync attempt")
        if attempt is None:
            return Attempt.impossible(f"Node {self.node._id} not yet computed")
        return attempt

    def to_formula_lines(self) -> list[FormulaLine]:
        attempt = self.node.latest_attempt()
        label = self._label
        if attempt is None:
            return [FormulaLine(label=label, value="—")]
        if attempt.succeeded:
            return [FormulaLine(label=label, value=self._format_value(attempt.value))]
        if attempt.impossible:
            return [FormulaLine(label=label, value="❌ " + (attempt.error or "failed"))]
        return [FormulaLine(label=label, value="⏳ pending")]


# ── Arithmetic Expressions ──


class Add(Expression):
    """Sum of one or more terms."""

    def __init__(self, *terms: Expression, description: str = ""):
        self.terms = terms
        self.description = description

    def evaluate(self) -> Attempt:
        total = None
        for term in self.terms:
            result = term.evaluate()
            if not result.succeeded:
                return Attempt.impossible(result.error or "expression failed")
            if total is None:
                total = result.value
            else:
                try:
                    total = total + result.value
                except TypeError as e:
                    return Attempt.impossible(f"Cannot add: {e}")
        if total is None:
            return Attempt.impossible("No terms to add")
        return Attempt.succeeded(total)

    def to_formula_lines(self) -> list[FormulaLine]:
        lines: list[FormulaLine] = []
        for term in self.terms:
            lines.extend(term.to_formula_lines())
        return lines


class Sub(Expression):
    """Subtract right from left."""

    def __init__(self, left: Expression, right: Expression, description: str = ""):
        self.left = left
        self.right = right
        self.description = description

    def evaluate(self) -> Attempt:
        left_result = self.left.evaluate()
        if not left_result.succeeded:
            return Attempt.impossible(left_result.error or "left operand failed")
        right_result = self.right.evaluate()
        if not right_result.succeeded:
            return Attempt.impossible(right_result.error or "right operand failed")
        try:
            return Attempt.succeeded(left_result.value - right_result.value)
        except TypeError as e:
            return Attempt.impossible(f"Cannot subtract: {e}")

    def to_formula_lines(self) -> list[FormulaLine]:
        return self.left.to_formula_lines() + self.right.to_formula_lines()


class Negate(Expression):
    """Negate a value."""

    def __init__(self, inner: Expression, description: str = ""):
        self.inner = inner
        self.description = description

    def evaluate(self) -> Attempt:
        result = self.inner.evaluate()
        if not result.succeeded:
            return result
        try:
            return Attempt.succeeded(-result.value)
        except TypeError as e:
            return Attempt.impossible(f"Cannot negate: {e}")

    def to_formula_lines(self) -> list[FormulaLine]:
        attempt = Ref(self.inner).evaluate() if hasattr(self.inner, "node") else Attempt.pending()
        return [FormulaLine(label="−", value="")]


class Mul(Expression):
    """Multiply left by right."""

    def __init__(self, left: Expression, right: Expression, description: str = ""):
        self.left = left
        self.right = right
        self.description = description

    def evaluate(self) -> Attempt:
        left_result = self.left.evaluate()
        if not left_result.succeeded:
            return Attempt.impossible(left_result.error or "left operand failed")
        right_result = self.right.evaluate()
        if not right_result.succeeded:
            return Attempt.impossible(right_result.error or "right operand failed")
        try:
            return Attempt.succeeded(left_result.value * right_result.value)
        except TypeError as e:
            return Attempt.impossible(f"Cannot multiply: {e}")

    def to_formula_lines(self) -> list[FormulaLine]:
        return self.left.to_formula_lines() + self.right.to_formula_lines()


class Div(Expression):
    """Divide left by right."""

    def __init__(self, left: Expression, right: Expression, description: str = ""):
        self.left = left
        self.right = right
        self.description = description

    def evaluate(self) -> Attempt:
        left_result = self.left.evaluate()
        if not left_result.succeeded:
            return Attempt.impossible(left_result.error or "left operand failed")
        right_result = self.right.evaluate()
        if not right_result.succeeded:
            return Attempt.impossible(right_result.error or "right operand failed")
        try:
            return Attempt.succeeded(left_result.value / right_result.value)
        except (ZeroDivisionError, TypeError) as e:
            return Attempt.impossible(f"Cannot divide: {e}")

    def to_formula_lines(self) -> list[FormulaLine]:
        return self.left.to_formula_lines() + self.right.to_formula_lines()


# ── Financial Expressions ──


class PMT(Expression):
    """Monthly mortgage payment = P × r(1+r)^n / ((1+r)^n − 1)."""

    def __init__(
        self,
        principal: Expression,
        annual_rate: Expression,
        term_years: Expression,
        description: str = "Monthly mortgage payment calculated from the loan amount, interest rate, and term",
    ):
        self.principal = principal
        self.annual_rate = annual_rate
        self.term_years = term_years
        self.description = description

    def evaluate(self) -> Attempt:
        p_result = self.principal.evaluate()
        if not p_result.succeeded:
            return Attempt.impossible(p_result.error or "principal missing")
        r_result = self.annual_rate.evaluate()
        if not r_result.succeeded:
            return Attempt.impossible(r_result.error or "rate missing")
        t_result = self.term_years.evaluate()
        if not t_result.succeeded:
            return Attempt.impossible(t_result.error or "term missing")

        p = p_result.value
        if isinstance(p, Money):
            p = Decimal(str(p.amount))
        r_raw = r_result.value
        if isinstance(r_raw, Decimal):
            r = float(r_raw) / 12
        else:
            r = float(r_raw) / 12
        n = int(t_result.value) * 12

        if r == 0:
            payment = p / Decimal(str(n))
        else:
            payment = p * Decimal(str(r * (1 + r) ** n / ((1 + r) ** n - 1)))

        if isinstance(p_result.value, Money):
            payment = Money(str(round(payment, 2)), p_result.value.currency)

        return Attempt.succeeded(payment)

    def to_formula_lines(self) -> list[FormulaLine]:
        lines: list[FormulaLine] = []
        lines.extend(self.principal.to_formula_lines())
        rate_lines = self.annual_rate.to_formula_lines()
        for l in rate_lines:
            lines.append(FormulaLine(label=l.label + " ÷ 12", value=l.value))
        term_lines = self.term_years.to_formula_lines()
        for l in term_lines:
            lines.append(FormulaLine(label=l.label + " × 12", value=l.value))
        return lines


class StampDutyFn(Expression):
    """Calculate UK Stamp Duty Land Tax from a property price."""

    def __init__(
        self,
        price: Expression,
        description: str = "UK Stamp Duty Land Tax — a one-off tax paid when buying a property",
    ):
        self.price = price
        self.description = description

    def evaluate(self) -> Attempt:
        price_result = self.price.evaluate()
        if not price_result.succeeded:
            return price_result
        price = price_result.value_or_none()
        if price is None:
            return Attempt.impossible("No price available for stamp duty calculation")
        from houses.stamp_duty import stamp_duty_land_tax

        try:
            return Attempt.succeeded(stamp_duty_land_tax(price))
        except Exception as e:
            return Attempt.impossible(f"Stamp duty calculation failed: {e}")

    def to_formula_lines(self) -> list[FormulaLine]:
        return self.price.to_formula_lines()


# ── Control Flow Expressions ──


class Conditional(Expression):
    """Choose between two branches based on a predicate.

    The predicate is a zero-argument callable — typically a lambda
    that references ``self`` from the enclosing node method, e.g.:

        predicate=lambda: self._status_node.latest_attempt().value_or_none() == "Current"

    Both branches are included in formula output for transparency.
    """

    def __init__(
        self,
        predicate,
        if_true: Expression,
        if_false: Expression | None = None,
        description: str = "",
    ):
        self.predicate = predicate
        self.if_true = if_true
        self.if_false = if_false
        self.description = description

    def evaluate(self) -> Attempt:
        try:
            condition = self.predicate()
        except Exception as e:
            return Attempt.impossible(f"Condition failed: {e}")

        if condition:
            return self.if_true.evaluate()
        elif self.if_false is not None:
            return self.if_false.evaluate()
        else:
            return Attempt.succeeded(None)

    def to_formula_lines(self) -> list[FormulaLine]:
        try:
            condition = self.predicate()
        except Exception:
            condition = None

        lines: list[FormulaLine] = []
        if condition is True:
            lines.extend(self.if_true.to_formula_lines())
        elif self.if_false is not None:
            lines.extend(self.if_false.to_formula_lines())
        return lines


class TieredRate(Expression):
    """Marginal tax/rate calculation across multiple bands.

    Each tier is ``(from_inclusive, to_exclusive, rate)`` where ``to_exclusive``
    can be ``None`` for the final open-ended tier. The expression finds which
    tier the value falls in and computes:

        tax = (value - tier_start) * rate + tax_at_tier_start

    ``tax_at_tier_start`` is automatically computed from previous tiers so
    you don't need to specify bases manually.

    Example — stamp duty:

        TieredRate(self._price_node, tiers=[
            (0, 250000, 0),
            (250000, 925000, Decimal("0.05")),
            (925000, 1500000, Decimal("0.10")),
            (1500000, None, Decimal("0.12")),
        ])
    """

    def __init__(
        self,
        value,
        tiers: list[tuple],
        description: str = "",
    ):
        if isinstance(value, Expression):
            self.value = value
        elif hasattr(value, "latest_attempt"):
            from dag.expression import Ref

            self.value = Ref(value)
        else:
            self.value = Literal(value)
        self.tiers = tiers
        self.description = description

    def _tax_at(self, price: Decimal, tier_idx: int) -> tuple[Decimal, Decimal]:
        """Compute tax for a value in the given tier.

        Returns (tax_at_this_tier, cumulative_tax_including_this_tier).
        """
        lo, hi, rate = self.tiers[tier_idx]
        if hi is not None and price > Decimal(str(hi)):
            effective = Decimal(str(hi))
        else:
            effective = price
        taxable = effective - Decimal(str(lo))
        tier_tax = taxable * Decimal(str(rate))

        # Tax from all previous tiers at their maximum
        prev_tax = Decimal("0")
        for i in range(tier_idx):
            plo, phi, prate = self.tiers[i]
            pwidth = Decimal(str(phi)) - Decimal(str(plo)) if phi is not None else Decimal("0")
            prev_tax += pwidth * Decimal(str(prate))

        return tier_tax, tier_tax + prev_tax

    def evaluate(self) -> Attempt:
        val_result = self.value.evaluate()
        if not val_result.succeeded:
            return val_result
        raw = val_result.value
        if hasattr(raw, "amount"):
            price = Decimal(str(raw.amount))
        else:
            price = Decimal(str(raw))

        for i, (lo, hi, rate) in enumerate(self.tiers):
            lo_d = Decimal(str(lo))
            if hi is not None:
                hi_d = Decimal(str(hi))
            else:
                hi_d = None

            if price < lo_d:
                continue
            if hi_d is not None and price > hi_d:
                continue

            tier_tax, total_tax = self._tax_at(price, i)
            result = total_tax
            break
        else:
            return Attempt.impossible(f"Price {price} does not fall in any tier")

        if hasattr(raw, "amount"):
            result_money = Money(str(result), raw.currency)
            return Attempt.succeeded(result_money)
        return Attempt.succeeded(result)

    def to_formula_lines(self) -> list[FormulaLine]:
        val_result = self.value.evaluate()
        if not val_result.succeeded:
            return [FormulaLine(label="Rate calculation", value="failed")]

        raw = val_result.value
        if hasattr(raw, "amount"):
            price_d = Decimal(str(raw.amount))
        else:
            price_d = Decimal(str(raw))

        lines: list[FormulaLine] = []
        lines.append(FormulaLine(label="Property price", value=self._format_value(raw)))

        active_tier = None
        for i, (lo, hi, rate) in enumerate(self.tiers):
            lo_d = Decimal(str(lo))
            if hi is not None:
                hi_d = Decimal(str(hi))
            else:
                hi_d = None

            if price_d < lo_d:
                continue
            if hi_d is not None and price_d > hi_d:
                continue
            active_tier = i

            if lo_d == 0 and rate == 0:
                lines.append(FormulaLine(label=f"First £{hi_d:,.0f} at 0%", value="£0.00"))
            else:
                prev_total = Decimal("0")
                for j in range(i):
                    plo, phi, prate = self.tiers[j]
                    pwidth = Decimal(str(phi)) - Decimal(str(plo)) if phi is not None else Decimal("0")
                    prev_total += pwidth * Decimal(str(prate))
                    if prate > 0:
                        lines.append(FormulaLine(
                            label=f"  £{plo:,.0f} to £{phi:,.0f} at {float(prate)*100:.0f}%",
                            value=self._format_value(Money(str(prev_total), "GBP")),
                        ))

                taxable = price_d - lo_d
                tier_tax = taxable * Decimal(str(rate))
                pct = float(rate) * 100
                lines.append(FormulaLine(
                    label=f"£{lo_d:,.0f} to £{price_d:,.0f} at {pct:.0f}%",
                    value=self._format_value(Money(str(tier_tax + prev_total), "GBP")),
                ))
            break

        return lines


class Field(Expression):
    """Extract a dict key from a node's evaluated value."""

    def __init__(self, source: Expression, key: str):
        self.source = source
        self.key = key

    def evaluate(self):
        result = self.source.evaluate()
        if not result.succeeded:
            return result
        val = result.value
        if isinstance(val, dict):
            return Attempt.succeeded(val.get(self.key))
        return Attempt.impossible(f"Cannot extract field {self.key!r} from {type(val).__name__}")

    def to_formula_lines(self):
        return [FormulaLine(label=self.key, value="")]


class Attr(Expression):
    """Extract an attribute from a node's evaluated value."""

    def __init__(self, source: Expression, attr: str):
        self.source = source
        self.attr = attr

    def evaluate(self):
        result = self.source.evaluate()
        if not result.succeeded:
            return result
        val = result.value
        if hasattr(val, self.attr):
            return Attempt.succeeded(getattr(val, self.attr))
        return Attempt.impossible(f"Cannot access attr {self.attr!r} from {type(val).__name__}")
