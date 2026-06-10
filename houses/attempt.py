"""Generic three-state type: Succeeded / Pending / Impossible.

Distinguishes "not tried yet" from "tried and failed" — unlike
``Optional[T]`` where ``None`` is ambiguous.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Generic, TypeVar

T = TypeVar("T")
U = TypeVar("U")
R = TypeVar("R")


class _Status(Enum):
    SUCCEEDED = auto()
    PENDING = auto()
    IMPOSSIBLE = auto()


@dataclass(frozen=True)
class Attempt(Generic[T]):
    """Outcome of a fallible operation.

    Usage::

        result = Attempt.succeeded(value, "source-name")
        result = Attempt.pending()
        result = Attempt.impossible("source-name", "reason", exception)

    Check the state with ``is_succeeded`` / ``is_pending`` / ``is_impossible``,
    or handle all three exhaustively with ``match()``.
    """

    _status: _Status
    _value: T | None = None
    _source: str = ""
    _reason: str = ""
    _exception: BaseException | None = None

    # ── Constructors ──────────────────────────────────────────────

    @staticmethod
    def succeeded(value: T, source: str) -> Attempt[T]:
        """Construct a successful attempt with *value* from *source*."""
        return Attempt(_status=_Status.SUCCEEDED, _value=value, _source=source)

    @staticmethod
    def pending() -> Attempt[T]:
        """Construct a not-yet-attempted state."""
        return Attempt(_status=_Status.PENDING)

    @staticmethod
    def impossible(source: str, reason: str, exception: BaseException | None = None) -> Attempt[T]:
        """Construct a terminal failure from *source* with human-readable *reason*."""
        return Attempt(_status=_Status.IMPOSSIBLE, _source=source, _reason=reason, _exception=exception)

    # ── Predicates ────────────────────────────────────────────────

    @property
    def is_succeeded(self) -> bool:
        return self._status is _Status.SUCCEEDED

    @property
    def is_pending(self) -> bool:
        return self._status is _Status.PENDING

    @property
    def is_impossible(self) -> bool:
        return self._status is _Status.IMPOSSIBLE

    # ── Extraction ────────────────────────────────────────────────

    def get(self) -> T:
        """Unwrap the value.

        Raises ``ValueError`` if the attempt is not Succeeded.
        Prefer ``value_or()``, ``value_or_none()``, or ``match()``.
        """
        if self._status is _Status.SUCCEEDED:
            return self._value  # type: ignore[return-value]
        msg = f"Attempt.get() called on {self._status.name} attempt"
        raise ValueError(msg)

    def value_or(self, default: T) -> T:
        """Return the value if Succeeded, otherwise *default*."""
        if self._status is _Status.SUCCEEDED:
            return self._value  # type: ignore[return-value]
        return default

    def value_or_none(self) -> T | None:
        """Bridge to ``Optional[T]`` — returns the value or ``None``."""
        if self._status is _Status.SUCCEEDED:
            return self._value
        return None

    # ── Transform ─────────────────────────────────────────────────

    def map(self, fn: Callable[[T], U]) -> Attempt[U]:
        """Transform the value if Succeeded; pass through otherwise."""
        if self._status is _Status.SUCCEEDED:
            return Attempt.succeeded(fn(self._value), self._source)  # type: ignore[arg-type]
        return self  # type: ignore[return-value]

    def bind(self, fn: Callable[[T], Attempt[U]]) -> Attempt[U]:
        """Chain a fallible transform; ``fn`` returns ``Attempt[U]``."""
        if self._status is _Status.SUCCEEDED:
            return fn(self._value)  # type: ignore[arg-type]
        return self  # type: ignore[return-value]

    # ── Exhaustive match ──────────────────────────────────────────

    def match(
        self,
        succeeded: Callable[[T, str], R],
        pending: Callable[[], R],
        impossible: Callable[[str, str, BaseException | None], R],
    ) -> R:
        """Handle every state explicitly.

        All three callbacks must return the same type ``R``::

            message = attempt.match(
                succeeded=lambda val, src: f"Got {val} from {src}",
                pending=lambda: "Not yet tried",
                impossible=lambda src, reason, exc: f"Failed: {reason}",
            )
        """
        if self._status is _Status.SUCCEEDED:
            return succeeded(self._value, self._source)  # type: ignore[arg-type]
        if self._status is _Status.PENDING:
            return pending()
        return impossible(self._source, self._reason, self._exception)
