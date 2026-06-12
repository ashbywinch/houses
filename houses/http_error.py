"""Structured HTTP error carrying status, headers, and Retry-After metadata.

Retry logic can inspect ``retry_after`` without depending on the HTTP client
library.  Callers that catch ``httpx.HTTPStatusError`` should convert to this
type before propagating.
"""

from __future__ import annotations


class HttpError(Exception):
    """An HTTP request failed with a non-2xx status code.

    Carries enough information for retry logic and diagnostics.
    """

    def __init__(
        self,
        status: int,
        message: str = "",
        *,
        headers: dict[str, str] | None = None,
        body: str = "",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.body = body
        reason = message or _status_phrase(status)
        super().__init__(f"HTTP {status}: {reason}")

    @property
    def retry_after(self) -> float | None:
        """Retry-After value in seconds, or ``None`` if not present.

        Respects both ``Retry-After`` (HTTP/1.1) and ``retry-after`` forms.
        """
        raw = self.headers.get("Retry-After") or self.headers.get("retry-after")
        if raw is None:
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None

    def is_server_error(self) -> bool:
        """True for 5xx responses that may be transient."""
        return 500 <= self.status < 600

    def is_rate_limit(self) -> bool:
        """True for 429 Too Many Requests."""
        return self.status == 429


def _status_phrase(code: int) -> str:
    """Human-readable HTTP status phrase."""
    return {
        300: "Multiple Choices",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        429: "Too Many Requests",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
    }.get(code, "")
