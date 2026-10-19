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

import json as _json
import logging
import sys
import traceback as _traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal as _Decimal
from enum import Enum, StrEnum, auto
from typing import Any, TypeVar

from money import Money as _Money
from pydantic import Field, field_serializer

from serialization import SerializableAsDict, omit_when_empty, omit_when_none

T = TypeVar("T")
U = TypeVar("U")
R = TypeVar("R")

logger = logging.getLogger(__name__)


class RetryableStatus:
    """Which HTTP statuses make an error retryable — one policy point, so the
    retry decision reads where it is made."""

    TOO_MANY_REQUESTS = 429
    SERVER_ERROR_START = 500
    SERVER_ERROR_END = 600


class SourceType(StrEnum):
    API = "api"
    CALC = "calc"
    USER = "user"
    CONFIG = "config"
    GEOCODE = "geocode"
    DB = "db"


class FormulaLine(SerializableAsDict):
    """One line of a provenance formula."""

    label: str
    value: str


class Formula(SerializableAsDict):
    """A provenance formula: its lines and the computed result."""

    lines: list[FormulaLine]
    result: str


class AttemptError(SerializableAsDict):
    """Structured error attached to an impossible Attempt.

    Holds the **actual exception object** in memory (``exc``) so code can
    inspect ``.status``, ``.headers``, ``__cause__``, etc. without parsing
    strings. ``to_dict()`` is a JSON-safe projection for persistence and
    the API — it never includes the exception object itself.

    ``causes`` carries the errors of failed dependencies, so a parent's
    error chain is traversable structurally instead of by string matching.

    **User-facing vs internal:** ``message`` is the full internal chain
    (may contain node ids and ``dep failed`` markers — for logs and
    debugging). ``user_message`` is the friendly text safe to render in
    the UI. Services pass friendly text as the message; framework paths
    (dep chains, exception handlers) set ``user_message`` explicitly to
    the leaf's reason or ``str(exc)``. Serialization emits ``error``
    (user_message) and ``error_detail.message`` (internal).
    """

    code: str = "error"  # machine category: dep_failed | http_error | timeout | exception | no_data | ...
    message: str = ""  # internal message (may contain node ids / dep chains)
    user_message: str = ""  # friendly, UI-safe; defaults to message
    retryable: bool = False
    source: str = ""  # node id or service that produced the error
    # The exception object is in-memory only: the wire carries its type name.
    exc: BaseException | None = Field(default=None, exclude=True)
    exc_type: str = ""  # derived at dump time from exc — see the serializer below
    traceback: str = ""
    causes: list[AttemptError] = Field(default_factory=list)

    @property
    def display_message(self) -> str:
        """User-facing message safe to render in the UI.

        Resolution: explicit ``user_message`` if set, else the deepest
        cause's message (leaf reason), else the internal ``message``.
        Never contains node ids or ``dep failed`` framework markers
        unless an explicit user_message was set to them.
        """
        if self.user_message:
            return self.user_message
        if self.causes:
            return self.causes[0].display_message
        return self.message

    @field_serializer("exc_type")
    def _exc_type(self, _stored: str) -> str:
        """The wire's ``exc_type`` is the exception's class name, derived at
        dump time: a computed field would serialise last, and the wire order is
        part of the contract."""
        return type(self.exc).__name__ if self.exc is not None else ""

    @field_serializer("user_message")
    def _display_message(self, _stored: str) -> str:
        """The wire's ``user_message`` is the UI-safe text, not the raw field."""
        return self.display_message

    @classmethod
    def from_exception(cls, message: str, exc: BaseException | None, *, source: str = "") -> AttemptError:
        """Build an AttemptError from a caught exception, deriving code,
        retryable, and traceback from the exception's shape.

        ``message`` is the internal message (may include node context).
        ``user_message`` prefers the exception's explicit friendly text
        (``HttpError.user_message``) — never ``str(exc)`` when a client
        provided one, because HTTP error strings can embed raw response
        bodies.  Falls back to ``str(exc)`` as before.
        """
        classification = classify_exception(exc)
        tb = ""
        if exc is not None:
            tb = "".join(_traceback.format_exception(type(exc), exc, exc.__traceback__))
        user_message = ""
        if exc is not None:
            friendly = getattr(exc, "user_message", "")
            user_message = friendly if isinstance(friendly, str) and friendly else str(exc)
        return cls(
            code=classification.code,
            message=message,
            user_message=user_message or message,
            retryable=classification.retryable,
            source=source,
            exc=exc,
            traceback=tb,
        )

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> AttemptError:
        """Reconstruct an AttemptError from its JSON-safe projection.

        Used when loading a persisted node result: the structured error
        (code, causes, user_message) survives restarts so display_message
        still resolves to the friendly leaf reason instead of the raw
        node-id/dep chain. ``exc`` and ``traceback`` are not persisted
        (exception objects and frames are in-memory only).
        """
        return cls(
            code=d.get("code", "error"),
            message=d.get("message", ""),
            user_message=d.get("user_message", ""),
            retryable=d.get("retryable", False),
            source=d.get("source", ""),
            exc=None,
            traceback=d.get("traceback", ""),
            causes=tuple(cls.from_dict(c) for c in d.get("causes", [])),
        )


@dataclass(frozen=True)
class ExceptionClassification:
    """(code, retryable) verdict for an exception.

    Named so the DAG retry logic and AttemptError read the fields by
    meaning rather than by position.
    """

    code: str
    retryable: bool


def classify_exception(exc: BaseException | None) -> ExceptionClassification:
    """Map an exception to (code, retryable) without importing HTTP libs.

    Handles ``houses.http_error.HttpError`` (``.status``), httpx errors
    (``.response.status_code``), ``TimeoutError``, the gateway's
    ``DailyQuotaError`` (its ``is_daily_quota`` marker), and anything else. This is the single source
    of truth for retryability — the DAG retry logic and AttemptError both
    use it.
    """
    if exc is None:
        return ExceptionClassification("error", retryable=False)
    if isinstance(exc, TimeoutError):
        return ExceptionClassification("timeout", retryable=True)
    if getattr(exc, "is_daily_quota", False):
        # the API key's DAILY quota is gone (houses.apigw.DailyQuotaError
        # carries the marker) — retrying before UTC midnight is futile and
        # burns quota; permanent, with a clear reason
        return ExceptionClassification("daily_quota", retryable=False)
    status = getattr(exc, "status", None)
    if status is None and hasattr(exc, "response"):
        status = getattr(exc.response, "status_code", None)
    if status is not None:
        try:
            code = int(status)
        except (TypeError, ValueError) as e:
            # Non-numeric status means the error isn't an HTTP error —
            # falls through to the plain "exception" classification.
            logger.debug("status %r is not a numeric HTTP code; classifying as a plain exception: %s", status, e)
            return ExceptionClassification("exception", retryable=False)
        if code is not None:
            retryable = (
                code == RetryableStatus.TOO_MANY_REQUESTS
                or RetryableStatus.SERVER_ERROR_START <= code < RetryableStatus.SERVER_ERROR_END
            )
            return ExceptionClassification("http_error", retryable=retryable)
    return ExceptionClassification("exception", retryable=False)


def _active_exception() -> BaseException | None:
    """Return the currently-handled exception, if any.

    Uses ``sys.exc_info()`` which is only populated inside an ``except``
    block. Returns None outside one, so a plain
    ``Attempt.impossible("reason")`` costs nothing.
    """
    exc_type, exc, tb = sys.exc_info()
    return exc if (exc_type is not None and exc is not None and tb is not None) else None


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
    @staticmethod
    def succeeded(cls, value):
        raise AttributeError("Cannot set 'succeeded' — reserved by Attempt constructor/property")

    @property
    def pending(cls):
        return lambda: cls(_Status.PENDING)

    @pending.setter
    @staticmethod
    def pending(cls, value):
        raise AttributeError("Cannot set 'pending' — reserved by Attempt constructor/property")

    @property
    def impossible(cls):
        return lambda error="", error_info=None: cls(_Status.IMPOSSIBLE, error=error, error_info=error_info)

    @impossible.setter
    @staticmethod
    def impossible(cls, value):
        raise AttributeError("Cannot set 'impossible' — reserved by Attempt constructor/property")


# lucidlint: ignore latent-class cohesive core value type — constructors, serialization, and provenance methods all
class Attempt[T](metaclass=_AttemptMeta):
    """Three-state result: succeeded / pending / impossible.

    Usage::

        Attempt.succeeded(value)
        Attempt.pending()
        Attempt.impossible("reason")

    Check state with the ``.succeeded`` / ``.pending`` / ``.impossible``
    properties.  For exhaustive handling, use ``.match()``.
    """

    __slots__ = ("_status", "_value", "_error", "_metadata", "_created_at", "_error_info")

    # Annotated so pyrefly knows the slot types (the metaclass + the
    # object.__setattr__ construction hide them from inference).
    _status: _Status
    _value: T | None
    _error: str
    _metadata: dict
    _created_at: datetime
    _error_info: AttemptError | None

    _now: Callable[[], datetime] = lambda: datetime.now(UTC)

    # lucidlint: ignore record-shape wire-format dict — serialization boundary
    def __init__(
        self,
        status: _Status,
        value: T | None = None,
        error: str = "",
        metadata: dict | None = None,
        *,
        error_info: AttemptError | None = None,
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
        object.__setattr__(self, "_created_at", Attempt._now())
        # except block (e.g. a service doing `except Exception as e:
        # return Attempt.impossible(...)`). Keeps the traceback for
        # debugging without putting it in the user-facing error string.
        if error_info is None and status is _Status.IMPOSSIBLE:
            exc = _active_exception()
            if exc is not None:
                error_info = AttemptError.from_exception(error, exc)
            else:
                # No exception captured — still give consumers a uniform
                # structured error so they never branch on None.
                error_info = AttemptError(code="no_data", message=error)
        object.__setattr__(self, "_error_info", error_info)

    @property
    def error_info(self) -> AttemptError | None:
        """Structured error (code, retryable, exception, traceback, causes).

        None for succeeded/pending attempts and for impossible attempts
        built with only a plain string message outside an except block.
        """
        return self._error_info

    @property
    def traceback(self) -> str:
        """Formatted traceback of the captured exception, if any.

        Empty string when the Attempt was not created inside an active
        except block (e.g. a plain `Attempt.impossible("reason")`).
        """
        info = self._error_info
        return info.traceback if info is not None else ""

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
        return self._value if self.succeeded else default  # type: ignore[return-value]  # _value is T | None (pending/impossible attempts hold no value); pyrefly can't narrow it through the `succeeded` property, so the succeeded branch still types as T | None against `-> T`

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
        return self._value  # type: ignore[return-value]  # same property-narrowing gap: `get()` guards with `if not self.succeeded: raise`, but pyrefly doesn't correlate `succeeded` with `_value`'s None-ness

    # ── Transform ─────────────────────────────────────────────────────
    def map(self, fn: Callable[[T], U]) -> Attempt[U]:
        """Transform the value if Succeeded; pass through otherwise."""
        if self.succeeded:
            return Attempt.succeeded(fn(self._value))  # type: ignore[arg-type]  # fn expects T, but _value is typed T | None and pyrefly can't narrow it via the `succeeded` property; this branch is only reached when a value exists
        return self  # type: ignore[return-value]  # Attempt[T] vs Attempt[U]: T is an invariant TypeVar, but this branch is only reached when not succeeded — no T or U value is present, which the checker can't express

    def bind(self, fn: Callable[[T], Attempt[U]]) -> Attempt[U]:
        """Chain a fallible transform; ``fn`` returns ``Attempt[U]``."""
        if self.succeeded:
            return fn(self._value)  # type: ignore[arg-type]  # fn expects T, but _value is typed T | None and pyrefly can't narrow it via the `succeeded` property; this branch is only reached when a value exists
        return self  # type: ignore[return-value]  # Attempt[T] vs Attempt[U]: T is an invariant TypeVar, but this branch is only reached when not succeeded — no T or U value is present, which the checker can't express

    def to_provenance(
        self,
        *,
        label: str,
        url: str = "",
        source_type: SourceType | None = None,
        formula: Formula | None = None,
        sources: dict[str, Provenance] | None = None,
    ) -> Provenance:
        """This attempt's provenance: its own status, error and value.

        The Attempt owns those fields, so the mapping lives here; a node
        supplies only what it knows — its label, source category, formula
        and the dep subtrees it read.
        """
        info = self._error_info
        user_error = info.display_message if info is not None else self._error
        return Provenance(
            label=label,
            description=user_error if self.impossible else None,
            value=project_value(self._value),
            url=url,
            source_type=source_type,
            formula=formula,
            status=self.status if self.impossible or self.pending else "",
            error=user_error if self.impossible else "",
            sources=sources or {},
        )

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
            return on_succeeded(self._value)  # type: ignore[arg-type]  # on_succeeded expects T, but _value is typed T | None and pyrefly can't narrow it via the `succeeded` property; this branch is only reached when a value exists
        if self.pending:
            return on_pending()
        return on_impossible(self._error)

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    # lucidlint: ignore record-shape wire-format dict — serialization boundary
    def metadata(self) -> dict:
        """Arbitrary metadata attached at construction (JSON-safe).

        Scenario evaluations mark hypothetical inputs with
        ``{"hypothetical": True}`` so provenance can render "not saved".
        """
        return self._metadata

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


def _project_non_json(v: Any) -> Any:
    """Project a value that failed the JSON probe through its type-specific path."""
    if isinstance(v, _Money):
        return str(v)
    if isinstance(v, _Decimal):
        # Decimal settings (petrol cost, mortgage rate) are value types
        # with a canonical float projection for display.
        return float(v)
    if isinstance(v, dict):
        return {k: project_value(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [project_value(item) for item in v]
    proj = getattr(v, "to_provenance_value", None)
    if callable(proj):
        return project_value(proj())
    raise TypeError(
        f"value of type {type(v).__name__} has no provenance projection; "
        "add to_provenance_value() to it or project it in build_provenance"
    )


def project_value(v: Any) -> Any:
    """Project a node value to a JSON-safe form for provenance display.

    - JSON-safe values pass through unchanged.
    - Money serialises as its string form ("GBP 800,000.00") — the
      canonical provenance convention.
    - dicts are projected recursively (per-person Money estimates).
    - Objects with a ``to_provenance_value()`` method (Commute, Person,
      GeoPoint, ...) are projected through it.
    - Anything else raises: silently dropping or repr-dumping a value
      would hide a missing serialization path.
    """
    if v is None:
        return None
    try:
        _json.dumps(v)
        return v
    except (TypeError, ValueError, OverflowError) as e:
        # The JSON probe is a type test: non-serializable values take the
        # type-specific projection path below — an ignorable, expected miss.
        logger.debug(
            "value of type %s is not JSON-serializable; projecting via its type-specific path: %s",
            type(v).__name__,
            e,
        )
        return _project_non_json(v)

class Provenance(SerializableAsDict):
    """Tracks where a value came from.

    Built dynamically by walking the DAG — not stored on Attempt objects.
    Each node's ``build_provenance()`` returns a Provenance that describes
    its source label and may include sub-sources from dependency nodes.

    Every ``value`` must be JSON-safe — nodes project rich domain objects
    through ``project_value`` before attaching them.
    """

    label: str = ""
    description: str | None = Field(default=None, exclude_if=omit_when_empty)
    url: str = Field(default="", exclude_if=omit_when_empty)
    value: Any = Field(default=None, exclude_if=omit_when_none)
    source_type: SourceType | None = Field(default=None, alias="sourceType", exclude_if=omit_when_none)
    status: str = Field(default="", exclude_if=omit_when_empty)
    error: str = Field(default="", exclude_if=omit_when_empty)
    freshness: datetime | None = Field(default=None, exclude_if=omit_when_none)
    formula: Formula | None = Field(default=None, exclude_if=omit_when_none)
    sources: dict[str, Provenance] = Field(default_factory=dict, exclude_if=omit_when_empty)

    @field_serializer("value")
    @staticmethod
    def _json_safe_value(value: Any) -> Any:
        """Values are projected to JSON-safe form at build time via
        ``project_value()``. Anything that still fails serialization here is a
        contract violation — fail fast so the emitting node is fixed, never
        silently drop or repr-dump the value. Money is the one canonical
        value-type exception: it has a well-defined string form."""
        try:
            _json.dumps(value)
        except (TypeError, ValueError, OverflowError):
            if isinstance(value, _Money):
                return str(value)
            raise TypeError(
                f"Provenance value of type {type(value).__name__} is not "
                "JSON-serializable; add to_provenance_value() to that type or "
                "project it in build_provenance"
            ) from None
        return value

    @field_serializer("source_type")
    @staticmethod
    def _source_type(source_type: SourceType | None) -> str | None:
        return source_type.value if source_type is not None else None

    @field_serializer("freshness")
    @staticmethod
    def _freshness(freshness: datetime | None) -> str | None:
        return freshness.isoformat() if freshness is not None else None

    @classmethod
    def from_label(cls, label: str, url: str = "") -> Provenance:
        """Create a simple leaf Provenance with just a label."""
        return cls(label=label, url=url)

    @classmethod
    def composite(cls, label: str, sources: dict[str, Provenance], url: str = "") -> Provenance:
        """Create a Provenance with dependency sub-sources."""
        return cls(label=label, sources=sources, url=url)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Provenance:
        """Reconstruct a Provenance from a ``to_dict()`` wire dict.

        The persistence round-trip for the frozen row. Tolerant of
        older rows (missing fields default; a malformed freshness is
        dropped, never a load failure).
        """
        freshness = data.get("freshness")
        if isinstance(freshness, str):
            try:
                freshness = datetime.fromisoformat(freshness)
            except ValueError as e:
                logger.warning("provenance has an unparseable freshness %r: %s", freshness, e)
                freshness = None
        formula = data.get("formula")
        formula_obj = None
        if isinstance(formula, dict):
            lines = [
                FormulaLine(label=str(line.get("label", "")), value=str(line.get("value", "")))
                for line in formula.get("lines", [])
            ]
            formula_obj = Formula(lines=lines, result=str(formula.get("result", "")))
        source_type = data.get("sourceType")
        return cls(
            label=str(data.get("label", "")),
            description=data.get("description"),
            value=data.get("value"),
            url=str(data.get("url", "")),
            source_type=SourceType(source_type) if source_type else None,
            freshness=freshness,
            formula=formula_obj,
            status=str(data.get("status", "")),
            error=str(data.get("error", "")),
            sources={k: cls.from_dict(v) for k, v in (data.get("sources") or {}).items()},
        )
