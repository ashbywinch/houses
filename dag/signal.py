"""A typed signal that fires when a node's value changes."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from dag.connection import Connection
from dag.slot import Slot


class Signal:
    """A typed signal that fires when a node's value changes.

    Handlers receive no arguments — they read the current value from
    the emitting node via ``.attempt()`` or ``.to_json()``.
    """

    def __init__(self) -> None:
        self._handlers: list[Callable] = []

    def connect(self, handler: Callable) -> Connection:
        """Register *handler*; the returned handle disconnects it."""
        if handler not in self._handlers:
            self._handlers.append(handler)
        return Connection(partial(self.disconnect, handler))

    def emit(self) -> None:
        """Call every handler, then drop the dead weak slots."""
        for handler in list(self._handlers):
            handler()
        # Sweep dead Slot handlers
        self._handlers = [h for h in self._handlers if not isinstance(h, Slot) or not h.is_dead()]

    def disconnect(self, handler: Callable) -> None:
        """Remove *handler* — the public seam a ``Connection`` calls."""
        if handler in self._handlers:
            self._handlers.remove(handler)
