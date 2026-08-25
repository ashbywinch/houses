"""Measurement — a value with an uncertainty, propagated through arithmetic.

Part A: uncertainty ("≈") as a first-class DAG citizen.

A ``Measurement[T]`` wraps a value (exact arithmetic on ``T``) with a
standard deviation. The arithmetic operators combine the VALUE through
``T``'s own operators and the ERROR through Gaussian error propagation
delegated to the ``uncertainties`` package — the same operator combines
both, so expressions built with plain ``+ - * /`` are correct by
construction.

Design rules (settled in the design chat — do not relitigate):

- Library-level and houses-agnostic: any node in any project can
  produce a Measurement.
- Exact = zero uncertainty. Mixing exact with approximate never widens
  or narrows the spread; estimates never quietly become facts.
- The stored value stays exact (``T`` — typically ``Decimal`` or
  ``Money``); only the uncertainty is float (``uncertainties`` is
  float-based, and a spread is display-precision anyway). The value is
  never round-tripped through float.
- Selection nodes need no uncertainty-specific logic: they return the
  chosen branch's value as-is, so the winner's uncertainty travels and
  the loser's never enters.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from operator import add as _add
from operator import mul as _mul
from operator import sub as _sub
from operator import truediv as _truediv
from typing import Any, Generic, TypeVar

from uncertainties import ufloat

from dag.attempt import project_value

T = TypeVar("T")


def _numeric(v: Any) -> float:
    """Extract a float numeric core for uncertainty propagation.

    ``Money`` and friends expose ``.amount``; plain numbers convert
    directly. The stored value itself is never converted — only the
    error propagation uses this.
    """
    if hasattr(v, "amount"):
        return float(v.amount)
    return float(v)


def _combine(op: Callable, a: Any, b: Any, sa: float, sb: float) -> float:
    """Combine two stddevs through a binary op via Gaussian propagation.

    Delegated to ``uncertainties`` (evaluated at the TRUE values, so
    value-dependent derivatives for ×/÷ are correct). When one side is
    exact the formula collapses to a scalar multiple of the surviving
    error, handled directly so no degenerate ``std_dev==0`` ufloat is
    constructed.
    """
    if sa == 0.0 and sb == 0.0:
        return 0.0
    if sa == 0.0 or sb == 0.0:
        na, nb = _numeric(a), _numeric(b)
        if op is _add or op is _sub:
            return sa if sa else sb
        if op is _mul:
            return nb * sa if sa else na * sb
        return sa / abs(nb) if sa else abs(na * sb / (nb * nb))
    ua = ufloat(_numeric(a), sa)
    ub = ufloat(_numeric(b), sb)
    return op(ua, ub).std_dev


@dataclass(frozen=True)
class Measurement(Generic[T]):
    """A value with an uncertainty (standard deviation)."""

    value: T
    stddev: float = 0.0

    # ── Binary arithmetic: value via T, error via uncertainties ──

    def __add__(self, other: Any) -> Measurement:
        if isinstance(other, Measurement):
            return Measurement(
                self.value + other.value,
                _combine(_add, self.value, other.value, self.stddev, other.stddev),
            )
        return self + Measurement(other)

# lucidlint: ignore middle-man protocol/reflected-operator requirement
    def __radd__(self, other: Any) -> Measurement:
        return self.__add__(other)  # addition is commutative

    def __sub__(self, other: Any) -> Measurement:
        if isinstance(other, Measurement):
            return Measurement(
                self.value - other.value,
                _combine(_sub, self.value, other.value, self.stddev, other.stddev),
            )
        return self - Measurement(other)

    def __rsub__(self, other: Any) -> Measurement:
        return Measurement(other) - self

    def __mul__(self, other: Any) -> Measurement:
        if isinstance(other, Measurement):
            return Measurement(
                self.value * other.value,
                _combine(_mul, self.value, other.value, self.stddev, other.stddev),
            )
        return self * Measurement(other)

# lucidlint: ignore middle-man protocol/reflected-operator requirement
    def __rmul__(self, other: Any) -> Measurement:
        return self.__mul__(other)  # multiplication is commutative

    def __truediv__(self, other: Any) -> Measurement:
        if isinstance(other, Measurement):
            return Measurement(
                self.value / other.value,
                _combine(_truediv, self.value, other.value, self.stddev, other.stddev),
            )
        return self / Measurement(other)

    def __rtruediv__(self, other: Any) -> Measurement:
        return Measurement(other) / self

    def __neg__(self) -> Measurement:
        return Measurement(-self.value, self.stddev)  # type: ignore[operator]  # T is an unconstrained TypeVar — the checker can't verify it supports unary `-` (pyrefly: "Unary - is not supported on T"); runtime T is always pint Quantity/numbers/Money, all of which define __neg__

    # ── Provenance / serialization ──────────────────────────────

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    def to_provenance_value(self) -> dict:
        """JSON-safe projection: ``{value, uncertainty}``.

        ``project_value`` handles the wrapped value's own projection
        (Money → its canonical string form, etc.).
        """
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        return {"value": project_value(self.value), "uncertainty": self.stddev}
