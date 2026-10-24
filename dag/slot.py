"""A bound-method handler held weakly, for auto-cleanup."""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable

logger = logging.getLogger(__name__)


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
