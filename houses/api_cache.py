"""Disk-backed cache for external API responses.

Cache entries are keyed by ``(method, url, params, body)`` so identical
requests return the cached response instead of re-hitting the API. The
cache is persistent across server restarts.

Usage in enrichment functions::

    from houses.api_cache import get_cached, set_cached

    key = ("GET", url, params, None)
    cached = get_cached(*key)
    if cached:
        return cached
    ...
    set_cached(*key, resp.json())
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

CACHE_DIR = Path("data/api_cache")


def set_cache_dir(path: str | Path) -> None:
    """Override the cache directory (used by tests to isolate caches)."""
    global CACHE_DIR  # type: ignore[global-statement]
    CACHE_DIR = Path(path)


def _make_key(method: str, url: str, params: dict[str, Any] | None, body: str | None) -> str:
    parts = [method.upper(), url]
    if params:
        parts.append(json.dumps(params, sort_keys=True))
    if body:
        parts.append(body)
    raw = "::".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def get_cached(
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    body: str | None = None,
) -> dict[str, Any] | None:
    """Return the cached JSON response for a request, or ``None``."""
    path = _cache_path(_make_key(method, url, params, body))
    if path.exists():
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    return None


def set_cached(method: str, url: str, params: dict[str, Any] | None, body: str | None, data: dict[str, Any]) -> None:
    """Store a JSON response so future identical requests skip the API."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(_make_key(method, url, params, body))
    path.write_text(json.dumps(data))


def with_cache_sync(
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    *,
    fetch,
) -> dict[str, Any]:
    """Sync version of ``with_cache`` — for use with ``httpx.Client``."""
    body_str = json.dumps(body, sort_keys=True) if body else None
    cached = get_cached(method, url, params, body_str)
    if cached is not None:
        return cached
    data = fetch()
    set_cached(method, url, params, body_str, data)
    return data


async def with_cache(
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    *,
    fetch,
) -> dict[str, Any]:
    """Check disk cache first; on miss call ``fetch``, cache result, return.

    ``fetch`` is an async callable that returns the parsed JSON dict.
    Example::

        data = await with_cache("GET", url, params=params, fetch=lambda: resp.json())
    """
    body_str = json.dumps(body, sort_keys=True) if body else None
    cached = get_cached(method, url, params, body_str)
    if cached is not None:
        return cached
    data = await fetch()
    set_cached(method, url, params, body_str, data)
    return data


class CachingTransport(httpx.AsyncBaseTransport):
    """httpx async transport that checks the disk cache before making HTTP calls.

    On a cache hit the stored JSON is returned directly.  On a miss the
    request is forwarded to ``_inner`` and the response is cached before
    being returned.

    Stores raw response bodies (not wrapped) so that enrichment functions
    using ``get_cached`` directly remain compatible.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None):
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        url_path = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()} if parsed.query else None
        body = request.content.decode() if request.content else None

        cached = get_cached(request.method, url_path, params, body)
        if cached is not None:
            return httpx.Response(200, json=cached)

        response = await self._inner.handle_async_request(request)
        try:
            data = response.json()
            set_cached(request.method, url_path, params, body, data)
        except Exception:
            pass
        return response


def cached_async_client(**kwargs) -> httpx.AsyncClient:
    """Return an ``AsyncClient`` that auto-caches every response to disk."""
    kwargs.setdefault("transport", CachingTransport())
    return httpx.AsyncClient(**kwargs)


def cached_sync_client(**kwargs) -> httpx.Client:
    """Return a ``Client`` that auto-caches every response to disk."""
    kwargs.setdefault("transport", CachingTransport())
    return httpx.Client(**kwargs)
