"""Shared helpers."""

from __future__ import annotations

import httpx

from dag.http_error import HttpError

HTTP_TOO_MANY_REQUESTS = 429
HTTP_5XX_START = 500
HTTP_5XX_END = 600


# lucidlint: ignore unused referenced only by the transient-retry regression tests — prod retry policy lives in
def is_transient_error(exc: Exception) -> bool:
    """True if the error is likely transient (rate limit, server error, network issue)."""
    if isinstance(exc, HttpError):
        return exc.is_rate_limit() or exc.is_server_error()
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == HTTP_TOO_MANY_REQUESTS or (HTTP_5XX_START <= status < HTTP_5XX_END)
    return isinstance(exc, (httpx.RequestError, httpx.TimeoutException, httpx.ConnectError))
