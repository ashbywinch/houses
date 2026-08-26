"""Per-request dependency context using contextvars.

Each getter auto-creates production defaults when the context variable
is not set (e.g. outside an HTTP request).  Tests explicitly set the
context variable to inject fakes or pre-populated state.
"""

from __future__ import annotations

import contextvars
from typing import Any

from houses.rightmove_scraper import scrape

_request_scrape_fn: contextvars.ContextVar[Any | None] = contextvars.ContextVar("_request_scrape_fn", default=None)


def get_scrape_fn() -> Any:
    """Return the per-request Rightmove scrape function.

    When the context variable is set (e.g. by a test fixture), returns
    that value.  Otherwise delegates to ``houses.rightmove_scraper.scrape``.
    """
    fn = _request_scrape_fn.get()
    if fn is not None:
        return fn
    return scrape
