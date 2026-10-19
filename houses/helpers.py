"""Shared helpers."""

from __future__ import annotations

import httpx

from dag.attempt import RetryableStatus
from dag.http_error import HttpError


# lucidlint: ignore unused referenced only by the transient-retry regression tests — prod retry policy lives in
def is_transient_error(exc: Exception) -> bool:
    """True if the error is likely transient (rate limit, server error, network issue)."""
    if isinstance(exc, HttpError):
        return exc.is_rate_limit() or exc.is_server_error()
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == RetryableStatus.TOO_MANY_REQUESTS or (
            RetryableStatus.SERVER_ERROR_START <= status < RetryableStatus.SERVER_ERROR_END
        )
    return isinstance(exc, (httpx.RequestError, httpx.TimeoutException, httpx.ConnectError))
