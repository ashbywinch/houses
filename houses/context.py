"""Per-request dependency context using contextvars.

Each getter auto-creates production defaults when the context variable
is not set (e.g. outside an HTTP request).  Tests explicitly set the
context variable to inject fakes or pre-populated state.
"""

from __future__ import annotations

import contextvars
from typing import Any

from houses.rightmove_scraper import scrape
from houses.sheets import get_client

_request_sheets_client: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "_request_sheets_client", default=None
)
_request_scrape_fn: contextvars.ContextVar[Any | None] = contextvars.ContextVar("_request_scrape_fn", default=None)
_request_get_client_fn: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "_request_get_client_fn", default=None
)


def get_scrape_fn() -> Any:
    """Return the per-request Rightmove scrape function.

    When the context variable is set (e.g. by a test fixture), returns
    that value.  Otherwise delegates to ``houses.rightmove_scraper.scrape``.
    """
    fn = _request_scrape_fn.get()
    if fn is not None:
        return fn
    return scrape


def get_client_factory() -> Any:
    """Return the per-request sheets client factory (callable() -> client).

    When the context variable is set (e.g. by a test fixture), returns
    that value.  Otherwise delegates to ``houses.sheets._real_get_client``.
    """
    fn = _request_get_client_fn.get()
    if fn is not None:
        return fn
    return get_client


def get_sheets_client() -> Any | None:
    """Return the per-request sheets client.

    When the context variable is set (e.g. by a test fixture), returns
    that value.  Otherwise delegates to ``houses.sheets._real_get_client``
    which manages the singleton ``gspread.Client``.
    """
    client = _request_sheets_client.get()
    if client is not None:
        return client
    return get_client()
