from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import TypeAdapter

from dag.attempt import Attempt, AttemptError, Provenance
from dag.expression import Add, Div, Expression, Literal, Mul, Negate, Ref, Sub
from dag.persistence import latest_node_result, save_node_result
from dag.signals import Signal


@dataclass
class NodeJson:
    """The serialized node record served by to_json/to_json_value and
    persisted into node_results. Fields left as None are omitted from the
    serialized dict, so the emitted key set matches the emitting path
    (full to_json vs to_json_value vs error-result)."""

    status: str
    value: Any = None
    succeeded: bool | None = None
    pending: bool | None = None
    impossible: bool | None = None
    error: str | None = None
    error_detail: dict | None = None
    source_url: str | None = None
    provenance: dict | None = None

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the node_results wire shape (coding-standards.md)
        d = dict(status=self.status, value=self.value)
        for k in ("succeeded", "pending", "impossible", "error", "error_detail", "source_url", "provenance"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d

T = TypeVar("T")

logger = logging.getLogger(__name__)


def _humanify(name: str) -> str:
    """Convert a code-style identifier to a human-friendly label.
    'computed_transit' -> 'Computed Transit'
    """
    return " ".join(w.capitalize() for w in name.replace("-", " ").split("_"))


# Cache TypeAdapters by value type to avoid OOM from creating thousands
# of Pydantic schemas at startup (~19s, 5GB for 6000 nodes).
# lucidlint: ignore global-state bounded module cache/state — single writer, deliberate
_adapter_cache: dict[type, TypeAdapter] = {}


def _get_adapter(t: type) -> TypeAdapter:
    if t not in _adapter_cache:
        _adapter_cache[t] = TypeAdapter(t)
    return _adapter_cache[t]


class PersistedNodeMixin(Generic[T]):
    """Persistence and timestamp contract shared by DAG nodes.

    Owns the DB round-trip state — when the node was computed or persisted,
    the DB row's created-at string, the dependency timestamps loaded from
    the last row, the persisted code version, and the retry budget — plus
    the load/persist operations that read and write them. ``Node`` mixes
    this in; the host class provides ``_id`` and ``_adapter``.
    """
    # Provided by the mixing-in host (``Node.__init__``) — declared here
    # so the mixin's own methods type-check against them.
    _id: str
    _adapter: TypeAdapter

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._computed_at: datetime | None = None
        self._persisted_at: datetime | None = None
        self._db_created_at: str = ""
        # lucidlint: ignore duplicate-block field-default initializer table — each line seeds a distinct piece of
        self._loaded_dep_timestamps: dict[str, str] = {}
        self._persisted_code_version: str | None = None
        self._retry_at: datetime | None = None
        self._retry_count: int = 0
        self._max_retries: int = 3

    def _load_attempt_from_db(self) -> Attempt[T] | None:
        stored = latest_node_result(self._id)
        if stored is None:
            return None

        # Unconditionally restore the DB timestamp before any early return.
        # This ensures downstream nodes' _is_stale() dep-timestamp checks
        # don't spuriously fire when we can't deserialize the value.
        persisted = stored.get("_persisted_at", "")
        if persisted:
            self._persisted_at = self._parse_persisted_at(persisted)
        self._db_created_at = persisted
        self._computed_at = self._persisted_at
        self._persisted_code_version = stored.get("_code_version") or ""
        dep_ts = stored.get("_dep_timestamps")
        if dep_ts:
            self._loaded_dep_timestamps = dep_ts

        status = stored.get("status", "")
        if status == "succeeded":
            try:
                value = stored["value"]
                # The selector's to_json (and legacy model rows) persist
                # commute legs under `details` (the frontend key); the
                # adapter reads `_details`. Map so the round-trip never
                # silently drops them.
                if (
                    isinstance(value, dict)
                    and isinstance(value.get("person"), dict)
                    and "_details" not in value
                    and "details" in value
                ):
                    value["_details"] = value.pop("details")
                val = self._adapter.validate_python(value)
            # lucidlint: ignore broad-except validation failure → Attempt.impossible carrying the reason
            except Exception as exc:
                return Attempt.impossible(f"validation error: {exc}")
            return Attempt.succeeded(val)
        if status == "pending":
            retry_at = stored.get("retry_at")
            if retry_at:
                dt = datetime.fromisoformat(retry_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                self._retry_at = dt
                self._retry_count = stored.get("retry_count", 0)
            return None

        # Reconstruct the structured error from the persisted error_detail
        # (code, causes, user_message) so display_message resolves to the
        # friendly leaf reason across restarts — not the raw node-id/dep
        # chain that the flat error string carries.
        detail = stored.get("error_detail")
        if detail and isinstance(detail, dict):
            return Attempt.impossible(
                stored.get("error", "unknown"),
                error_info=AttemptError.from_dict(detail),
            )
        # Legacy rows (persisted before error_detail existed): no structured
        # info to recover — keep the raw string; provenance fallback makes
        # it generic-friendly when rendered.
        return Attempt.impossible(stored.get("error", "unknown"))

    def _parse_persisted_at(self, persisted: str) -> datetime | None:
        """Parse the DB timestamp; None when malformed.

        A malformed timestamp means the staleness dep-timestamp check
        skips this node rather than spuriously firing — degradable.
        """
        try:
            dt = datetime.fromisoformat(persisted)
            # Ensure offset-aware — DB may have stored naive timestamps
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except (ValueError, TypeError) as e:
            logger.debug(
                "%s: unparseable persisted_at %r; skipping the staleness dep-check: %s",
                self._id,
                persisted,
                e,
            )
            return None

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    def _persist(
        self,
        result_dict: dict,
        dep_timestamps: dict[str, str] | None = None,
        *,
        code_version: str | None = None,
    ) -> None:
        now_str = datetime.now(UTC).isoformat()
        save_node_result(self._id, result_dict, dep_timestamps, created_at=now_str, code_version=code_version)
        now = datetime.fromisoformat(now_str)
        self._persisted_at = now
        self._db_created_at = now_str
        if code_version is not None:
            self._persisted_code_version = code_version
        if dep_timestamps is not None:
            self._loaded_dep_timestamps = dep_timestamps


class Node(ABC, PersistedNodeMixin[T], Generic[T]):
    """Base class for all DAG nodes."""

    def __init__(self, node_id: str, value_type: type[T], source_url: str = "") -> None:
        self._id: str = node_id
        self._value_type: type[T] = value_type
        self._source_url: str = source_url
        self._adapter: TypeAdapter = _get_adapter(value_type)
        self.changed: Signal = Signal()
        raw = node_id.rstrip("/").split("/")[-1]
        self.display_name: str = _humanify(raw)
        super().__init__()

    def latest_attempt(self) -> Attempt:
        """Synchronous access to the last known attempt.
        Override in subclasses that cache the attempt object."""
        raise RuntimeError(f"{type(self).__name__} does not support sync attempt access")

    @staticmethod
    @abstractmethod
    async def attempt() -> Attempt[T]:
        """Compute or retrieve the current value."""
        ...

    @staticmethod
    @abstractmethod
    async def build_provenance() -> Provenance:
        """Build provenance by walking dependency nodes.
        Subclasses override this to return a Provenance describing
        how this node's value was derived."""
        ...
    # lucidlint: ignore record-shape to_json returns the serialized node record (coding-standards.md)
    async def to_json(self) -> dict:
        attempt = await self.attempt()
        rec = NodeJson(
            status=attempt.status,
            value=self._adapter.dump_python(attempt.value, mode="json") if attempt.succeeded else None,
            succeeded=attempt.succeeded,
            pending=attempt.pending,
            impossible=attempt.impossible,
        )
        if attempt.impossible:
            info = attempt.error_info
            rec.error = (info.display_message if info is not None else attempt.error) or attempt.error
            if info is not None:
                rec.error_detail = info.to_dict()
        if self._source_url:
            rec.source_url = self._source_url
        if not attempt.pending:
            rec.provenance = (await self.build_provenance()).to_dict()
        return rec.to_dict()

    # lucidlint: ignore record-shape returns the serialized node record (coding-standards.md)
    async def to_json_value(self) -> dict:
        """Like to_json() but skips the expensive provenance tree build.

        Returns status, value, and metadata only — no ``provenance`` field.
        Use this for bulk-list endpoints where provenance is not needed.
        """
        attempt = await self.attempt()
        rec = NodeJson(
            status=attempt.status,
            value=self._adapter.dump_python(attempt.value, mode="json") if attempt.succeeded else None,
            succeeded=attempt.succeeded,
            pending=attempt.pending,
            impossible=attempt.impossible,
        )
        if attempt.impossible:
            info = attempt.error_info
            rec.error = (info.display_message if info is not None else attempt.error) or attempt.error
        if self._source_url:
            rec.source_url = self._source_url
        return rec.to_dict()

    # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    def _impossible(self, dep_attempts: dict[str, Attempt[Any]], extra: str = "") -> Attempt[T]:
        parts = [self._id]
        if extra:
            parts.append(extra)
        parts.extend(
            f"{name}: not available" if attempt is None else f"{name}: {attempt.error or 'unknown'}"
            for name, attempt in dep_attempts.items()
            if attempt is None or not attempt.succeeded
        )
        causes = [
            attempt.error_info
            for name, attempt in dep_attempts.items()
            if attempt is not None and not attempt.succeeded and attempt.error_info is not None
        ]
        message = "; ".join(parts)
        if causes:
            return Attempt.impossible(
                message,
                error_info=AttemptError(
                    code="dep_failed",
                    message=message,
                    source=self._id,
                    causes=tuple(causes),
                ),
            )
        return Attempt.impossible(message)




# ── Expression operators ────────────────────────────────
# These let you use Node objects directly in expressions:
#   self._price_node + self._stamp_duty_node - self._equity_node
# instead of wrapping each in Ref().
# Expression classes are imported at module top from dag.expression.


def _to_expr(value):
    """Convert a plain value to an Expression if it isn't already one."""
    if isinstance(value, Expression):
        return value
    if isinstance(value, Node):
        return Ref(value)
    return Literal(value)


def _with_ops(self, other):
    return _to_expr(self), _to_expr(other)


def _node_binop(expr_cls):
    """Build a binary Node operator that coerces both sides to Expressions."""
    def op(self, other):
        a, b = _with_ops(self, other)
        return expr_cls(a, b)
    return op


def _node_neg(self):
    return Negate(Ref(self))


Node.__add__ = _node_add = _node_binop(Add)
Node.__radd__ = _node_add  # same — addition is commutative
Node.__sub__ = _node_binop(Sub)
Node.__rsub__ = lambda self, other: _to_expr(other) - _to_expr(self)
Node.__neg__ = _node_neg
Node.__mul__ = _node_binop(Mul)
Node.__rmul__ = _node_binop(Mul)  # same — multiplication is commutative
Node.__truediv__ = _node_binop(Div)
Node.__rtruediv__ = lambda self, other: _to_expr(other) / _to_expr(self)
