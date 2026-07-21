from __future__ import annotations

import weakref
from collections.abc import Callable


class Connection:
    """Represents a connected handler. Call ``.disconnect()`` to remove it."""

    def __init__(self, signal: Signal, handler: Callable) -> None:
        self._signal = signal
        self._handler = handler

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
        self._handlers = [
            h for h in self._handlers
            if not isinstance(h, Slot) or not h.is_dead()
        ]

    def _disconnect(self, handler: Callable) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)


class Slot:
    """Wraps a bound method with a weak reference for auto-cleanup."""

    def __init__(self, callback: Callable) -> None:
        self._callback: Callable | None = None
        try:
            self._ref = weakref.WeakMethod(callback)
        except TypeError:
            self._ref = None
            self._callback = callback

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
            self._callback()
