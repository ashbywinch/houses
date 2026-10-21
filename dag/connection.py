"""A connected handler's handle."""

from __future__ import annotations

from collections.abc import Callable


class Connection:
    """Represents a connected handler. Call ``.disconnect()`` to remove it.

    The handle holds the teardown it was given — the signal's own public
    ``disconnect``, bound to the handler — so it never reaches into the
    signal's internals. That is the shape RxPY's ``Disposable(action)`` and
    the stdlib's ``weakref.finalize`` take: a handle that runs the callable it
    was handed.
    """

    def __init__(self, teardown: Callable[[], None]) -> None:
        self._teardown: Callable[[], None] = teardown

    def disconnect(self) -> None:
        """Remove the handler this handle was created for."""
        self._teardown()
