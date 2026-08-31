from __future__ import annotations

import logging
import weakref
from collections.abc import Callable

logger = logging.getLogger(__name__)


class Connection:
    """Represents a connected handler. Call ``.disconnect()`` to remove it."""

    def __init__(self, signal: Signal, handler: Callable) -> None:
        self._signal: Signal = signal
        self._handler: Callable = handler

    def disconnect(self) -> None:
        self._signal._disconnect(self._handler)


class Signal:
    """A typed signal that fires when a node's value changes.

    Handlers receive no arguments — they read the current value from
    the emitting node via ``.attempt()`` or ``.to_json()``.
    """

    def __init__(self) -> None:
        self._handlers: list[Callable] = []

    def connect(self, handler: Callable) -> Connection:
        if handler not in self._handlers:
            self._handlers.append(handler)
        return Connection(self, handler)

    def emit(self) -> None:
        for handler in list(self._handlers):
            handler()
        # Sweep dead Slot handlers
        self._handlers = [h for h in self._handlers if not isinstance(h, Slot) or not h.is_dead()]

    def _disconnect(self, handler: Callable) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)


class Slot:
    """Wraps a bound method with a weak reference for auto-cleanup."""

    _ref: weakref.WeakMethod | None = None

    def __init__(self, callback: Callable) -> None:
        self._callback: Callable | None = None
        try:
            self._ref = weakref.WeakMethod(callback)
        except TypeError as e:
            # WeakMethod only accepts bound methods — non-method callables
            # (lambdas, functions, partials) fall back to a strong ref.
            logger.debug("callback %r is not a bound method; holding a strong reference instead: %s", callback, e)
            self._ref = None
            self._callback = callback
            return

    def is_dead(self) -> bool:
        """Return True when the underlying handler is no longer reachable."""
        if self._ref is not None:
            return self._ref() is None
        # Non-method callables: Slot holds a strong ref, so never dead
        return False

    def __call__(self) -> None:
        if self._ref is not None:
            cb = self._ref()
            if cb is not None:
                cb()
        else:
            cb = self._callback
            if cb is not None:
                cb()
