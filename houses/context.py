"""Per-request dependency context using contextvars.

Each getter auto-creates production defaults when the context variable
is not set (e.g. outside an HTTP request).  Tests explicitly set the
context variable to inject fakes or pre-populated state.
"""

from __future__ import annotations

import contextvars
from typing import Any

_request_scrape_fn: contextvars.ContextVar[Any | None] = contextvars.ContextVar("_request_scrape_fn", default=None)

