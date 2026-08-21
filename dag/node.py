from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import TypeAdapter

from dag.attempt import Attempt, AttemptError, Provenance
from dag.signals import Signal

T = TypeVar("T")


def _humanify(name: str) -> str:
    """Convert a code-style identifier to a human-friendly label.
    'computed_transit' -> 'Computed Transit'
    """
    return " ".join(w.capitalize() for w in name.replace("-", " ").split("_"))


# Cache TypeAdapters by value type to avoid OOM from creating thousands
# of Pydantic schemas at startup (~19s, 5GB for 6000 nodes).
_adapter_cache: dict[type, TypeAdapter] = {}


def _get_adapter(t: type) -> TypeAdapter:
    if t not in _adapter_cache:
        _adapter_cache[t] = TypeAdapter(t)
    return _adapter_cache[t]


class Node(ABC, Generic[T]):
    """Base class for all DAG nodes."""

    def __init__(self, node_id: str, value_type: type[T], source_url: str = "") -> None:
        self._id = node_id
        self._value_type = value_type
        self._source_url = source_url
        self._adapter = _get_adapter(value_type)
        self.changed = Signal()
        raw = node_id.rstrip("/").split("/")[-1]
        self.display_name: str = _humanify(raw)
        self._computed_at: datetime | None = None
        self._persisted_at: datetime | None = None
        self._db_created_at: str = ""
        self._loaded_dep_timestamps: dict[str, str] = {}
        self._persisted_code_version: str | None = None
        self._retry_at: datetime | None = None
        self._retry_count: int = 0
        self._max_retries: int = 3

    def _load_attempt_from_db(self) -> Attempt[T] | None:
        from dag.persistence import latest_node_result

        stored = latest_node_result(self._id)
        if stored is None:
            return None

        # Unconditionally restore the DB timestamp before any early return.
        # This ensures downstream nodes' _is_stale() dep-timestamp checks
        # don't spuriously fire when we can't deserialize the value.
        persisted = stored.get("_persisted_at", "")
        if persisted:
            try:
                dt = datetime.fromisoformat(persisted)
                # Ensure offset-aware — DB may have stored naive timestamps
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                self._persisted_at = dt
            except (ValueError, TypeError):
                self._persisted_at = None
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
            from dag.attempt import AttemptError

            return Attempt.impossible(
                stored.get("error", "unknown"),
                error_info=AttemptError.from_dict(detail),
            )
        # Legacy rows (persisted before error_detail existed): no structured
        # info to recover — keep the raw string; provenance fallback makes
        # it generic-friendly when rendered.
        return Attempt.impossible(stored.get("error", "unknown"))

    def latest_attempt(self) -> Attempt:
        """Synchronous access to the last known attempt.
        Override in subclasses that cache the attempt object."""
        raise RuntimeError(f"{type(self).__name__} does not support sync attempt access")

    @abstractmethod
    async def attempt(self) -> Attempt[T]:
        """Compute or retrieve the current value."""
        ...

    @abstractmethod
    async def build_provenance(self) -> Provenance:
        """Build provenance by walking dependency nodes.
        Subclasses override this to return a Provenance describing
        how this node's value was derived."""
        ...

    async def to_json(self) -> dict:
        attempt = await self.attempt()
        result: dict = {
            "status": attempt.status,
            "value": self._adapter.dump_python(attempt.value, mode="json") if attempt.succeeded else None,
        }
        result["succeeded"] = attempt.succeeded
        result["pending"] = attempt.pending
        result["impossible"] = attempt.impossible
        if attempt.impossible:
            info = attempt.error_info
            result["error"] = (info.display_message if info is not None else attempt.error) or attempt.error
            if info is not None:
                result["error_detail"] = info.to_dict()
        if self._source_url:
            result["source_url"] = self._source_url
        if not attempt.pending:
            result["provenance"] = (await self.build_provenance()).to_dict()
        return result

    async def to_json_value(self) -> dict:
        """Like to_json() but skips the expensive provenance tree build.

        Returns status, value, and metadata only — no ``provenance`` field.
        Use this for bulk-list endpoints where provenance is not needed.
        """
        attempt = await self.attempt()
        result: dict = {
            "status": attempt.status,
            "value": self._adapter.dump_python(attempt.value, mode="json") if attempt.succeeded else None,
        }
        result["succeeded"] = attempt.succeeded
        result["pending"] = attempt.pending
        result["impossible"] = attempt.impossible
        if attempt.impossible:
            info = attempt.error_info
            result["error"] = (info.display_message if info is not None else attempt.error) or attempt.error
        if self._source_url:
            result["source_url"] = self._source_url
        return result

    def _persist(self, result_dict: dict, dep_timestamps: dict[str, str] | None = None) -> None:
        from dag.persistence import save_node_result

        now_str = datetime.now(UTC).isoformat()
        from dag.derived_node import DerivedNode

        code_version = self._current_code_version() if isinstance(self, DerivedNode) else None
        save_node_result(self._id, result_dict, dep_timestamps, created_at=now_str, code_version=code_version)
        now = datetime.fromisoformat(now_str)
        self._persisted_at = now
        self._db_created_at = now_str
        if code_version is not None:
            self._persisted_code_version = code_version
        if dep_timestamps is not None:
            self._loaded_dep_timestamps = dep_timestamps

    def _impossible(self, dep_attempts: dict[str, Attempt[Any]], extra: str = "") -> Attempt[T]:
        parts = [self._id]
        if extra:
            parts.append(extra)
        causes: list[AttemptError] = []
        for name, attempt in dep_attempts.items():
            if attempt is None:
                parts.append(f"{name}: not available")
            elif not attempt.succeeded:
                detail = attempt.error or "unknown"
                parts.append(f"{name}: {detail}")
                if attempt.error_info is not None:
                    causes.append(attempt.error_info)
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
# Each method uses lazy imports to avoid circular imports.


def _to_expr(value):
    """Convert a plain value to an Expression if it isn't already one."""
    from dag.expression import Expression, Literal, Ref

    if isinstance(value, Expression):
        return value
    if isinstance(value, Node):
        return Ref(value)
    return Literal(value)


def _with_ops(self, other):
    return _to_expr(self), _to_expr(other)


def _node_add(self, other):
    from dag.expression import Add

    a, b = _with_ops(self, other)
    return Add(a, b)


def _node_sub(self, other):
    from dag.expression import Sub

    a, b = _with_ops(self, other)
    return Sub(a, b)


def _node_neg(self):
    from dag.expression import Negate, Ref

    return Negate(Ref(self))


def _node_mul(self, other):
    from dag.expression import Mul

    a, b = _with_ops(self, other)
    return Mul(a, b)


def _node_div(self, other):
    from dag.expression import Div

    a, b = _with_ops(self, other)
    return Div(a, b)


Node.__add__ = _node_add
Node.__radd__ = _node_add  # same — addition is commutative
Node.__sub__ = _node_sub
Node.__rsub__ = lambda self, other: _to_expr(other) - _to_expr(self)
Node.__neg__ = _node_neg
Node.__mul__ = _node_mul
Node.__rmul__ = _node_mul  # same — multiplication is commutative
Node.__truediv__ = _node_div
Node.__rtruediv__ = lambda self, other: _to_expr(other) / _to_expr(self)
