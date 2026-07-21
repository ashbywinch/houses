from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from datetime import UTC, datetime, timedelta
from inspect import iscoroutine
from typing import Generic, TypeVar

from dag.attempt import Attempt, Provenance
from dag.node import Node
from dag.scheduler import _get_scheduler
from dag.signals import Connection, Slot

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DerivedNode(Node[T], Generic[T]):
    """A node whose value is computed from other nodes."""

    def __init__(self, node_id: str, value_type: type[T], deps: tuple[Node, ...]) -> None:
        super().__init__(node_id, value_type)
        self._deps = deps
        self._attempt: Attempt[T] = Attempt.pending()
        self._connections: list[Connection] = []
        self._slots: list[Slot] = []

        loaded = self._load_attempt_from_db()
        if loaded is not None:
            self._attempt = loaded
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

    @property
    def _skip_impossible_dep_check(self) -> bool:
        """Override in subclasses whose compute() handles failed deps gracefully.

        When True, the generic impossible-dep short-circuit in refresh() is
        skipped, allowing compute() to receive impossible dep attempts and
        handle them (e.g., IfThenElseNode falls back to else branch,
        CommuteSelectorNode falls back to bus when transit fails).
        """
        return False

    def _is_transient_error(self, exc: Exception) -> bool:
        """Override in subclasses to identify retryable errors.

        When True, ``refresh()`` calls ``schedule_retry()`` and returns
        ``Attempt.pending()`` instead of ``Attempt.impossible()``.
        The default returns False (no retry for any error).
        """
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

    async def refresh(self) -> None:
        if not self._is_stale():
            return
        active_deps = self._get_active_deps()
        dep_attempts = [await dep.attempt() for dep in active_deps]
        if any(a.pending for a in dep_attempts):
            return
        if not self._skip_impossible_dep_check:
            impossible_deps = [a for a in dep_attempts if a.impossible]
            if impossible_deps:
                errors = "; ".join(a.error or "unknown" for a in impossible_deps)
                self._attempt = Attempt.impossible(f"dep failed: {errors}")
                self._computed_at = datetime.now(UTC)
                self._retry_at = None  # dep is permanently gone, cancel retry
                dep_timestamps = {dep._id: dep._db_created_at for dep in active_deps}
                result_dict = {
                    "status": "impossible",
                    "value": None,
                    "error": f"dep failed: {errors}",
                    "provenance": await self._build_provenance_dict(),
                }
                self._persist(result_dict, dep_timestamps)
                self.changed.emit()
                _get_scheduler().after_refresh(self)
                return
        try:
            result = self.compute(*dep_attempts)
            if iscoroutine(result):
                result = await result
                # Yield to event loop so HTTP requests aren't starved during
                # burst refresh (many nodes queued at startup).
                await asyncio.sleep(0)
        except Exception as e:
            if self._is_transient_error(e):
                if not self.schedule_retry(self._retry_delay_from(e)):
                    result = Attempt.impossible(f"{self._id}: retry exhausted ({e})")
                else:
                    result = Attempt.pending()
            else:
                result = Attempt.impossible(f"{self._id}: {e}")
        # Yield after compute finishes so the event loop can serve HTTP
        # requests before we do sync persist work (json.dumps + SQLite).
        await asyncio.sleep(0)

        self._attempt = result
        self._computed_at = datetime.now(UTC)

        dep_timestamps = {dep._id: dep._db_created_at for dep in active_deps}

        if result.pending:
            try:
                result_dict = await self.to_json()
            except Exception as e:
                result_dict = {
                    "status": "pending",
                    "value": None,
                    "error": str(e),
                    "provenance": await self._build_provenance_dict(),
                }
            self._persist(result_dict, dep_timestamps)
            return

        self._retry_at = None
        self._retry_count = 0 if result.succeeded else self._retry_count

        try:
            result_dict = await self.to_json()
        except Exception as e:
            result_dict = {
                "status": "impossible",
                "value": None,
                "error": str(e),
                "provenance": await self._build_provenance_dict(),
            }
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
            sources[dep._id] = await dep.build_provenance()
        description = self._attempt.error
        return Provenance(
            label=self.display_name,
            description=description,
            value=self._attempt.value,
            sources=sources,
        )

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

    @abstractmethod
    def compute(self, *dep_attempts: Attempt) -> Attempt[T]: ...
