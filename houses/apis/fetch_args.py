"""The guarded call's parameter object — one external call's what-and-where."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FetchArgs:
    """One guarded external call: what to send and where.

    A parameter object for the transport seam — every API method builds
    exactly one of these, then `BaseApi._fetch` translates the transport
    errors.
    """

    method: str
    url: str
    params: Any = None
    body: Any = None
    headers: dict[str, str] | None = None
    wire_params: dict[str, str] | None = None
    _client_factory: Any = None
    no_cache: bool = False