from __future__ import annotations

import asyncio
import logging
import traceback
from abc import abstractmethod
from datetime import UTC, datetime, timedelta
from inspect import iscoroutine
from typing import Generic, TypeVar

from dag.attempt import Attempt, Formula, Provenance, SourceType
from dag.expression import Expression
from dag.http_error import HttpError
from dag.node import Node
from dag.scheduler import _get_scheduler
from dag.signals import Connection, Slot

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DerivedNode(Node[T], Generic[T]):
    """A node whose value is computed from other nodes."""

    # ── Expression system ────────────────────────────────
    # Subclasses set ``expression`` (or override it as a ``@property``)
    # to an ``Expression`` tree. If set, ``build_provenance`` uses it
    # for formula generation.

    _expression: Expression | None = None

    @property
    def expression(self) -> Expression | None:
        return self._expression

    def _build_formula_from_expression(self) -> Formula | None:
        """Build a Formula from the expression tree, if one is set."""
        expr = self.expression
        if expr is None:
            return None
        try:
            return expr.to_formula()
        except Exception:
            return None

    def __init__(self, node_id: str, value_type: type[T], deps: tuple[Node, ...], source_url: str = "") -> None:
        super().__init__(node_id, value_type, source_url)
        self._deps = deps
        self._attempt: Attempt[T] = Attempt.pending()
        self._connections: list[Connection] = []
        self._slots: list[Slot] = []

        loaded = self._load_attempt_from_db()
        if loaded is not None:
            self._attempt = loaded
        for i, dep in enumerate(deps):
            if dep is None:
                raise ValueError(
                    f"{self._id}: dependency at index {i} is None — "
                    f"DAG nodes must not have None dependencies. "
                    f"Check the caller's deps list."
                )
        for dep in deps:
            slot = Slot(self._on_dep_changed)
            self._slots.append(slot)
            conn = dep.changed.connect(slot)
            self._connections.append(conn)

        _get_scheduler().register(self)

    def disconnect(self) -> None:
        """Disconnect all signal connections and unregister from the scheduler."""
        for conn in self._connections:
            conn.disconnect()
        self._connections.clear()
        _get_scheduler().unregister(self)

    def _get_active_deps(self) -> tuple[Node, ...]:
        return self._deps

    def latest_attempt(self) -> Attempt:
        return self._attempt

    def _on_dep_changed(self) -> None:
        self._retry_count = 0
        if not self._is_stale():
            return
        _get_scheduler().schedule(self)

    def _is_stale(self) -> bool:
        if self._retry_at is not None:
            return True
        if self._attempt.pending:
            return True
        for dep in self._get_active_deps():
            if (
                dep._persisted_at is not None
                and self._computed_at is not None
                and dep._persisted_at > self._computed_at
            ):
                logger.warning(
                    "STALE1: %s dep=%s persisted=%s > computed=%s",
                    self._id,
                    dep._id,
                    dep._persisted_at.isoformat(),
                    self._computed_at.isoformat(),
                )
                return True
            if (
                isinstance(dep, DerivedNode)
                and dep._computed_at is not None
                and self._computed_at is not None
                and dep._computed_at > self._computed_at
            ):
                logger.warning(
                    "STALE2: %s dep=%s computed=%s > self_computed=%s",
                    self._id,
                    dep._id,
                    dep._computed_at.isoformat(),
                    self._computed_at.isoformat(),
                )
                return True
            if self._loaded_dep_timestamps:
                stored = self._loaded_dep_timestamps.get(dep._id, "")
                if stored:
                    if not dep._db_created_at:
                        logger.warning(
                            "STALE_EMPTY: %s dep=%s has empty _db_created_at",
                            self._id,
                            dep._id,
                        )
                        continue
                    if dep._db_created_at != stored:
                        logger.warning(
                            "STALE3: %s dep=%s stored=%s actual=%s",
                            self._id,
                            dep._id,
                            stored,
                            dep._db_created_at,
                        )
                        return True
        return False

    async def attempt(self) -> Attempt[T]:
        return self._attempt

    def _is_transient_error(self, exc: Exception) -> bool:
        """Identify retryable transient errors (TimeoutError, HttpError rate-limit/server-error, status-aware)."""
        if isinstance(exc, asyncio.TimeoutError):
            return True
        if isinstance(exc, HttpError):
            return exc.is_rate_limit() or exc.is_server_error()
        if hasattr(exc, "status"):
            try:
                return int(exc.status) in (429, 502, 503, 504)
            except (ValueError, TypeError):
                pass
        return False

    def schedule_retry(self, delay: timedelta) -> bool:
        """Schedule a DAG-level retry at now + delay.

        Returns True if the retry was scheduled, False if max retries exceeded.
        When False, the caller should return ``Attempt.impossible`` instead of
        ``Attempt.pending()`` so the node doesn't stay pending forever.
        """
        if self._retry_count >= self._max_retries:
            return False
        self._retry_at = datetime.now(UTC) + delay
        _get_scheduler().schedule_at(self, self._retry_at)
        return True

    def _retry_delay_from(self, exc: Exception, base_delay: timedelta = timedelta(seconds=10)) -> timedelta:
        """Extract retry delay from an exception, or use exponential backoff."""
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            return timedelta(seconds=min(retry_after, 300))
        delay_sec = base_delay.total_seconds() * (2**self._retry_count)
        if self._retry_count < self._max_retries:
            self._retry_count += 1
        return timedelta(seconds=min(delay_sec, 300))

    async def _error_result_dict(self, status: str, exc: Exception) -> dict:
        return {
            "status": status,
            "value": None,
            "error": f"{exc}\n{traceback.format_exc()}",
            "provenance": await self._build_provenance_dict(),
        }

    async def refresh(self) -> None:
        if not self._is_stale():
            return
        active_deps = self._get_active_deps()
        for i, dep in enumerate(active_deps):
            if dep is None:
                raise ValueError(
                    f"{self._id}._get_active_deps() returned None at index {i}. "
                    f"Active deps: {[d._id if d else None for d in active_deps]}"
                )
        dep_attempts = [await dep.attempt() for dep in active_deps]
        # Propagate impossible before checking pending — if a dep is
        # impossible and another is pending, fail fast rather than
        # waiting indefinitely for the pending dep to resolve.
        impossible_deps = [a for a in dep_attempts if a.impossible]
        if impossible_deps:
            errors = "; ".join(a.error or "unknown" for a in impossible_deps)
            result = Attempt.impossible(f"{self._id}: dep failed ({errors})")
            self._attempt = result
            self._computed_at = datetime.now(UTC)
            # _db_created_at may be None for deps never persisted (e.g. a
            # freshly-created node).  Storing None means the next staleness
            # check skips this dep — the _computed_at comparison still works.
            dep_timestamps = {dep._id: dep._db_created_at for dep in active_deps}
            self._retry_at = None
            self._retry_count = 0
            try:
                result_dict = await self.to_json()
            except Exception as e:
                result_dict = await self._error_result_dict("impossible", e)
            self._persist(result_dict, dep_timestamps)
            self.changed.emit()
            _get_scheduler().after_refresh(self)
            return
        if any(a.pending for a in dep_attempts):
            return
        try:
            result = self.compute(*dep_attempts)
            if iscoroutine(result):
                result = await result
                # Yield to event loop so HTTP requests aren't starved during
                # burst refresh (many nodes queued at startup).
                await asyncio.sleep(0)
        except Exception as e:
            _tb_str = traceback.format_exc()
            if self._is_transient_error(e):
                if not self.schedule_retry(self._retry_delay_from(e)):
                    result = Attempt.impossible(f"{self._id}: retry exhausted ({e})\n{_tb_str}")
                else:
                    result = Attempt.pending()
            else:
                impossible_deps = [a for a in dep_attempts if a.impossible]
                if impossible_deps:
                    errors = "; ".join(a.error or "unknown" for a in impossible_deps)
                    result = Attempt.impossible(f"{self._id}: dep failed ({errors})\n{_tb_str}")
                else:
                    result = Attempt.impossible(f"{self._id}: {e}\n{_tb_str}")
        # requests before we do sync persist work (json.dumps + SQLite).
        await asyncio.sleep(0)

        self._attempt = result
        self._computed_at = datetime.now(UTC)

        # _db_created_at may be None for deps never persisted (e.g. a
        # freshly-created node).  Storing None means the next staleness
        # check skips this dep — the _computed_at comparison still works.
        dep_timestamps = {dep._id: dep._db_created_at for dep in active_deps}

        if result.pending:
            try:
                result_dict = await self.to_json()
            except Exception as e:
                result_dict = await self._error_result_dict("pending", e)
            self._persist(result_dict, dep_timestamps)
            return

        self._retry_at = None
        self._retry_count = 0 if result.succeeded else self._retry_count

        try:
            result_dict = await self.to_json()
        except Exception as e:
            result_dict = await self._error_result_dict("impossible", e)
        self._persist(result_dict, dep_timestamps)
        self.changed.emit()
        _get_scheduler().after_refresh(self)

    async def _build_provenance_dict(self) -> dict:
        """Build provenance dict for persistence, with a fallback if build_provenance() fails."""
        try:
            prov = await self.build_provenance()
            return prov.to_dict()
        except Exception:
            return {"label": ""}

    async def build_provenance(self) -> Provenance:
        sources: dict[str, Provenance] = {}
        for dep in self._get_active_deps():
            try:
                sources[dep._id] = await dep.build_provenance()
            except Exception as e:
                sources[dep._id] = Provenance(
                    label=getattr(dep, "display_name", dep._id),
                    description=f"build_provenance failed: {e}\n{traceback.format_exc()}",
                )

        # Use expression system for formula if available
        formula = self.provenance_formula
        if formula is None:
            formula = self._build_formula_from_expression()

        description = self._attempt.error if self._attempt.impossible else None
        status = "impossible" if self._attempt.impossible else ("pending" if self._attempt.pending else "")
        return Provenance(
            label=self.display_name,
            description=description,
            value=self._attempt.value,
            url=self._source_url,
            source_type=self.provenance_source_type,
            freshness=self._attempt.created_at,
            formula=formula,
            status=status,
            error=self._attempt.error if self._attempt.impossible else "",
            sources=sources,
        )

    @property
    def provenance_source_type(self) -> SourceType:
        """Subclass declares its data source category.
        Default is CALC — override in subclasses that source from
        APIs, geocoding, config, or user input."""
        return SourceType.CALC

    @property
    def provenance_formula(self) -> Formula | None:
        """Override to return a Formula for computed values.
        Default is None — no formula."""
        return None

    async def to_json(self) -> dict:
        result = await super().to_json()
        if self._retry_at is not None:
            result["retry_at"] = self._retry_at.isoformat()
            result["retry_count"] = self._retry_count
        if not self._attempt.pending:
            result["stale"] = self._is_stale()
        return result

    async def to_json_value(self) -> dict:
        result = await super().to_json_value()
        if self._retry_at is not None:
            result["retry_at"] = self._retry_at.isoformat()
            result["retry_count"] = self._retry_count
        if not self._attempt.pending:
            result["stale"] = self._is_stale()
        return result

    @staticmethod
    def _assert_deps_succeeded(**deps: Attempt) -> None:
        """Assert all named dependencies are succeeded.

        Auto-propagation in _refresh() catches impossible/pending deps
        before compute() is ever called.  This assertion is a safety net
        to fail fast if that contract is violated.
        """
        failed = {name: att.status for name, att in deps.items() if att is not None and not att.succeeded}
        if failed:
            raise AssertionError(
                f"Dependencies not succeeded: {failed}. Auto-propagation should have caught these before compute()."
            )

    @abstractmethod
    def compute(self, *dep_attempts: Attempt) -> Attempt[T]: ...
