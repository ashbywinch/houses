"""HTTP options for a Google Routes POST: key, timeout, and test seams.

One class per module: the options record is a public dataclass, so it lives
apart from the client that defaults them — the wire records stay with the
client, whose contract they are.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GoogleRoutesOptions:
    """HTTP options for a Google Routes POST: key, timeout, and test seams.

    The cache/client seams default to the module implementations so tests
    never monkeypatch ``houses.commute_router`` globals.
    """

    timeout: float = 10.0
    api_key: str | None = None
    client_factory: Callable[..., Any] | None = None
    set_cached_fn: Callable[..., Any] | None = None
