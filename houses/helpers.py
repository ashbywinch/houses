"""Shared helpers."""

from __future__ import annotations

import httpx

from dag.http_error import HttpError


def is_transient_error(exc: Exception) -> bool:
    """True if the error is likely transient (rate limit, server error, network issue)."""
    if isinstance(exc, HttpError):
        return exc.is_rate_limit() or exc.is_server_error()
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or (500 <= status < 600)
    return isinstance(exc, (httpx.RequestError, httpx.TimeoutException, httpx.ConnectError))
