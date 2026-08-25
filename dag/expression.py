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

import logging
from abc import ABC, abstractmethod
from copy import deepcopy
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine
from dag.measurement import Measurement

if TYPE_CHECKING:
    from dag.node import Node as _Node

T = TypeVar("T")

logger = logging.getLogger(__name__)


class Expression(ABC, Generic[T]):
    """A declarative expression that can be evaluated AND walked for provenance."""

    description: str = ""
    """Plain-English explanation of what this expression does."""

    @staticmethod
    @abstractmethod
    def evaluate() -> Attempt[T]:
        """Evaluate this expression.

        Walks the expression tree, calling ``latest_attempt()`` on any
        referenced dependency nodes. Returns an ``Attempt``.
        """
        ...

    def _format_value(self, v: Any) -> str:
        """Format a Python value for formula display."""
        if isinstance(v, Money):
            return f"£{v.amount:,.2f}"
        if isinstance(v, Measurement):
            return f"{self._format_value(v.value)} ± {v.stddev:g}"
        if isinstance(v, Decimal):
            if v < Decimal("0.01"):
                return f"{v:.2%}"
            return f"£{v:,.2f}" if v > Decimal("100") else f"{v:,.2f}"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            return f"{v:,.2f}"
        return str(v)

    @staticmethod
    def to_formula_lines() -> list[FormulaLine]:
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
                except TypeError:
                    # Addition is commutative — a left operand whose
                    # __add__ raises (instead of returning NotImplemented)
                    # may still accept the reversed order (e.g. Money +
                    # Measurement). Fall back before declaring failure.
                    try:
                        total = result.value + total
                    except TypeError as e:
                        return Attempt.impossible(f"Cannot add: {e}")
        if total is None:
            return Attempt.impossible("No terms to add")
        return Attempt.succeeded(total)

    def to_formula_lines(self) -> list[FormulaLine]:
        return [line for term in self.terms for line in term.to_formula_lines()]

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
        left_value = left_result.value_or_none()
        right_value = right_result.value_or_none()
        if left_value is None or right_value is None:
            return Attempt.impossible("operand missing")
        try:
            return Attempt.succeeded(left_value - right_value)
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
        value = result.value_or_none()
        if value is None:
            return Attempt.impossible("operand missing")
        try:
            return Attempt.succeeded(-value)
        except TypeError as e:
            return Attempt.impossible(f"Cannot negate: {e}")

    @staticmethod
    def to_formula_lines() -> list[FormulaLine]:
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
        left_value = left_result.value_or_none()
        right_value = right_result.value_or_none()
        if left_value is None or right_value is None:
            return Attempt.impossible("operand missing")
        try:
            return Attempt.succeeded(left_value * right_value)
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
        left_val = left_result.value_or_none()
        right_val = right_result.value_or_none()
        if left_val is None or right_val is None:
            return Attempt.impossible("operand missing")
        try:
            # Handle string values that look like money ("124.80") by converting to Money
            if isinstance(left_val, str):
                left_val = Money(left_val, "GBP")
            if isinstance(right_val, str):
                right_val = Money(right_val, "GBP")
            return Attempt.succeeded(left_val / right_val)
        except (ZeroDivisionError, TypeError) as e:
            return Attempt.impossible(f"Cannot divide: {e}")

    def to_formula_lines(self) -> list[FormulaLine]:
        return self.left.to_formula_lines() + self.right.to_formula_lines()


# ── Financial Expressions ──






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
        # lucidlint: ignore broad-except boundary — predicate failure converts to an impossible attempt
        except Exception as e:
            return Attempt.impossible(f"Condition failed: {e}")

        if condition:
            return self.if_true.evaluate()
        elif self.if_false is not None:
            return self.if_false.evaluate()
        else:
            return Attempt.succeeded(None)

    def _condition_for_formula(self) -> bool | None:
        """Predicate for formula rendering; None when it cannot be evaluated
        (the display degrades to the false branch)."""
        try:
            return self.predicate()
        # lucidlint: ignore broad-except deliberate degrade — predicate failure during formula rendering yields None
        except Exception as e:
            logger.debug(
                "Conditional %r: predicate failed during formula rendering; treating as unknown: %s",
                self.description,
                e,
            )
            return None

    def to_formula_lines(self) -> list[FormulaLine]:
        condition = self._condition_for_formula()

        lines: list[FormulaLine] = []
        if condition is True:
            lines.extend(self.if_true.to_formula_lines())
        elif self.if_false is not None:
            lines.extend(self.if_false.to_formula_lines())
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
        if not isinstance(val, dict):
            return Attempt.impossible(f"Cannot extract field {self.key!r} from {type(val).__name__}")
        if self.key not in val:
            return Attempt.impossible(f"Key {self.key!r} not found")
        return Attempt.succeeded(val[self.key])

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
        if not hasattr(val, self.attr):
            return Attempt.impossible(f"Cannot access attr {self.attr!r} from {type(val).__name__}")
        v = getattr(val, self.attr)
        if v is None:
            return Attempt.impossible(f"Attr {self.attr!r} is None")
        return Attempt.succeeded(v)


class Choose(Expression):
    """Evaluates all alternatives and selects the best one.

    All alternatives are DAG node references that are already computed.
    The selector function receives ``{name: Attempt}`` and returns the
    name of the winner.  ``to_formula_lines()`` shows each alternative
    with its result, clearly indicating which was selected.

    Example — commute mode selection::

        Choose(
            alternatives={
                "walk": Ref(walk_node),
                "transit": Ref(transit_node),
                "drive": Ref(drive_node),
            },
            selector=lambda results: min(
                results, key=lambda k: results[k].value_or_none().duration
            ),
        )

    The selector can return ``None`` to indicate no valid choice.
    """

    def __init__(
        self,
        alternatives: dict[str, Expression],
        selector,
        description: str = "",
    ):
        self.alternatives = alternatives
        self.selector = selector
        self.description = description
        self.last_results: dict[str, Attempt] | None = None
        """Results dict from the most recent evaluate() call, exposed for provenance."""

    def evaluate(self):
        results: dict[str, Attempt] = {}
        for name, expr in self.alternatives.items():
            results[name] = expr.evaluate()
        self.last_results = results

        try:
            winner = self.selector(results)
        # lucidlint: ignore broad-except boundary — selector failure converts to an impossible attempt
        except Exception as e:
            return Attempt.impossible(f"Choose selector failed: {e}")

        if winner is None:
            return Attempt.impossible("Choose: no alternative selected")

        if winner not in results:
            return Attempt.impossible(f"Choose: selector returned unknown alternative {winner!r}")

        return results[winner]

    def _formula_winner(self) -> str | None:
        """Re-run the selector for formula display; None when it fails
        (no alternative is then marked as the winner)."""
        try:
            return self.selector(self.last_results)
        # lucidlint: ignore broad-except deliberate degrade — selector failure during formula rendering yields None
        except Exception as e:
            logger.debug("Choose: selector failed during formula rendering: %s", e)
            return None

    def to_formula_lines(self):
        if not self.last_results:
            return [FormulaLine(label="Choose", value="not evaluated")]

        lines: list[FormulaLine] = []
        # Determine winner by re-running selector on stored results
        winner = self._formula_winner()

        for name, attempt in self.last_results.items():
            if attempt.succeeded:
                val_str = self._format_value(attempt.value)
                prefix = "✓" if name == winner else "✗"
                lines.append(FormulaLine(label=f"{prefix} {name}", value=val_str))
            elif attempt.impossible:
                prefix = "✗"
                err = attempt.error or "failed"
                lines.append(FormulaLine(label=f"{prefix} {name}", value=f"❌ {err}"))
            else:
                lines.append(FormulaLine(label=f"⏳ {name}", value="pending"))

        return lines
