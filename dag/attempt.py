"""Three-state result type and provenance tracking.

``Attempt[T]`` represents a value that has succeeded, is pending
(not yet computed), or has failed (impossible).  Use the static
constructors (``succeeded``, ``pending``, ``impossible``) and query
with the ``.succeeded``, ``.pending``, ``.impossible`` properties.

``Provenance`` is a standalone dataclass that records where a value
came from.  It is computed dynamically by walking the DAG, not stored
on ``Attempt`` objects.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum, auto
from typing import Any, TypeVar

T = TypeVar("T")
U = TypeVar("U")
R = TypeVar("R")


class SourceType(StrEnum):
    API = "api"
    CALC = "calc"
    USER = "user"
    CONFIG = "config"
    GEOCODE = "geocode"
    DB = "db"


@dataclass
class FormulaLine:
    label: str
    value: str


@dataclass
class Formula:
    lines: list[FormulaLine]
    result: str


class _Status(Enum):
    SUCCEEDED = auto()
    PENDING = auto()
    IMPOSSIBLE = auto()


class _AttemptMeta(type):
    """Metaclass enabling ``Attempt.succeeded(value)`` class calls
    alongside ``instance.succeeded`` boolean properties.

    Python cannot have a ``@classmethod`` and a ``@property`` with
    the same name on one class.  This metaclass provides class-level
    properties for ``succeeded``, ``pending``, and ``impossible``.
    """

    @property
    def succeeded(cls):
        return lambda value, error="", **kwargs: cls(_Status.SUCCEEDED, value=value, error=error, **kwargs)

    @succeeded.setter
    def succeeded(cls, value):
        raise AttributeError("Cannot set 'succeeded' — reserved by Attempt constructor/property")

    @property
    def pending(cls):
        return lambda: cls(_Status.PENDING)

    @pending.setter
    def pending(cls, value):
        raise AttributeError("Cannot set 'pending' — reserved by Attempt constructor/property")

    @property
    def impossible(cls):
        return lambda error="": cls(_Status.IMPOSSIBLE, error=error)

    @impossible.setter
    def impossible(cls, value):
        raise AttributeError("Cannot set 'impossible' — reserved by Attempt constructor/property")


class Attempt[T](metaclass=_AttemptMeta):
    """Three-state result: succeeded / pending / impossible.

    Usage::

        Attempt.succeeded(value)
        Attempt.pending()
        Attempt.impossible("reason")

    Check state with the ``.succeeded`` / ``.pending`` / ``.impossible``
    properties.  For exhaustive handling, use ``.match()``.
    """

    __slots__ = ("_status", "_value", "_error", "_metadata", "_created_at")

    _now: Callable[[], datetime] = lambda: datetime.now(UTC)

    def __init__(
        self,
        status: _Status,
        value: T | None = None,
        error: str = "",
        metadata: dict | None = None,
        *,
        _now: Callable[[], datetime] | None = None,
    ) -> None:
        if status is _Status.SUCCEEDED and isinstance(value, Attempt):
            raise TypeError(
                f"Cannot create Attempt.succeeded() with an Attempt as value. "
                f"Attempt values must be domain objects, not Attempts. "
                f"Attempt inside Attempt: {type(value).__name__}"
                f" -> {type(value._value).__name__ if value._value is not None else 'None'}"
            )
        object.__setattr__(self, "_status", status)
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_error", error)
        object.__setattr__(self, "_metadata", metadata or {})
        object.__setattr__(self, "_created_at", (_now or Attempt._now)())

    # ── Predicates (instance properties) ─────────────────────────

    @property
    def succeeded(self) -> bool:
        """True when this Attempt holds a succeeded value."""
        return self._status is _Status.SUCCEEDED

    @property
    def pending(self) -> bool:
        """True when this Attempt is not yet computed."""
        return self._status is _Status.PENDING

    @property
    def impossible(self) -> bool:
        """True when this Attempt holds a failure/error."""
        return self._status is _Status.IMPOSSIBLE

    @property
    def status(self) -> str:
        """Serialisable status string: ``"succeeded"`` / ``"pending"`` / ``"impossible"``."""
        return "succeeded" if self.succeeded else "pending" if self.pending else "impossible"

    # ── Value access ──────────────────────────────────────────────────

    @property
    def value(self) -> T | None:
        """The wrapped value, or ``None`` if not succeeded."""
        return self._value

    @property
    def error(self) -> str:
        """Human-readable error message (empty string if succeeded)."""
        return self._error

    def value_or(self, default: T) -> T:
        """Return the value if succeeded, otherwise *default*."""
        return self._value if self.succeeded else default  # type: ignore[return-value]

    def value_or_none(self) -> T | None:
        """Bridge to ``Optional[T]`` — returns the value or ``None``."""
        return self._value if self.succeeded else None

    def get(self) -> T:
        """Unwrap the value.

        Raises ``ValueError`` if the attempt is not Succeeded.
        Prefer ``value_or()``, ``value_or_none()``, or ``match()``.
        """
        if not self.succeeded:
            raise ValueError(f"Attempt.get() called on {self._status.name}")
        return self._value  # type: ignore[return-value]

    # ── Transform ─────────────────────────────────────────────────────
    def map(self, fn: Callable[[T], U]) -> Attempt[U]:
        """Transform the value if Succeeded; pass through otherwise."""
        if self.succeeded:
            return Attempt.succeeded(fn(self._value))  # type: ignore[arg-type]
        return self  # type: ignore[return-value]

    def bind(self, fn: Callable[[T], Attempt[U]]) -> Attempt[U]:
        """Chain a fallible transform; ``fn`` returns ``Attempt[U]``."""
        if self.succeeded:
            return fn(self._value)  # type: ignore[arg-type]
        return self  # type: ignore[return-value]

    # ── Exhaustive match ──────────────────────────────────────────────
    def match(
        self,
        on_succeeded: Callable[[T], R],
        on_pending: Callable[[], R],
        on_impossible: Callable[[str], R],
    ) -> R:
        """Handle every state explicitly.

        All three callbacks must return the same type ``R``::

            msg = attempt.match(
                on_succeeded=lambda val: f"Got {val}",
                on_pending=lambda: "Not yet tried",
                on_impossible=lambda err: f"Failed: {err}",
            )
        """
        if self.succeeded:
            return on_succeeded(self._value)  # type: ignore[arg-type]
        if self.pending:
            return on_pending()
        return on_impossible(self._error)

    @property
    def created_at(self) -> datetime:
        return self._created_at

    # ── Equality ───────────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Attempt):
            return NotImplemented
        return self._status is other._status and self._value == other._value and self._error == other._error

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self) -> int:
        return hash((self._status, self._value, self._error))


@dataclass
class Provenance:
    """Tracks where a value came from.

    Built dynamically by walking the DAG — not stored on Attempt objects.
    Each node's ``build_provenance()`` returns a Provenance that describes
    its source label and may include sub-sources from dependency nodes.
    """

    label: str = ""
    description: str = ""
    value: Any = None
    url: str = ""
    source_type: SourceType | None = None
    freshness: datetime | None = None
    formula: Formula | None = None
    sources: dict[str, Provenance] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict."""
        result: dict = {"label": self.label}
        if self.description:
            result["description"] = self.description
        if self.url:
            result["url"] = self.url
        if self.value is not None:
            # Omit the value if it's not JSON-serializable or too large
            try:
                import json as _json

                _json.dumps(self.value)
                result["value"] = self.value
            except (TypeError, ValueError, OverflowError):
                result["value"] = str(self.value)
        if self.source_type is not None:
            result["sourceType"] = self.source_type.value
        if self.freshness is not None:
            result["freshness"] = self.freshness.isoformat()
        if self.formula is not None:
            result["formula"] = {
                "lines": [{"label": line.label, "value": line.value} for line in self.formula.lines],
                "result": self.formula.result,
            }
        if self.sources:
            result["sources"] = {k: v.to_dict() for k, v in self.sources.items()}
        return result

    @classmethod
    def from_label(cls, label: str, url: str = "") -> Provenance:
        """Create a simple leaf Provenance with just a label."""
        return cls(label=label, url=url)

    @classmethod
    def composite(cls, label: str, sources: dict[str, Provenance], url: str = "") -> Provenance:
        """Create a Provenance with dependency sub-sources."""
        return cls(label=label, sources=sources, url=url)
